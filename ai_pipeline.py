"""
ai_pipeline.py — AI pipeline for PawPal+

Pipeline flow:
    retrieve_context()
        → generate_ai_schedule()   (AI Planner)
        → evaluate_schedule()      (AI Evaluator)
        → refine loop if needed
        → apply_ai_schedule()      (writes into existing Schedule._placed)

Supporting components:
    log_pipeline_step()    — Logger
    run_reliability_tests() — Testing System (no API calls)
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Tuple

from groq import Groq

from pawpal_system import Pet, Schedule, Task, User

# ---------------------------------------------------------------------------
# JSON extraction helper — robust against markdown fences and extra prose
# ---------------------------------------------------------------------------
def _extract_json(text: str) -> Dict | None:
    """
    Find and parse the first complete JSON object in an AI response.
    Handles markdown code fences, leading/trailing prose, and nested braces.
    Returns a dict on success, None on failure.
    """
    import re
    text = text.strip()

    # 1. Strip markdown fences if present
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fenced:
        text = fenced.group(1).strip()

    # 2. Find the first '{' and match it to its closing '}'
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None

    return None


# ---------------------------------------------------------------------------
# Knowledge Base — Retriever data source
# ---------------------------------------------------------------------------
KNOWLEDGE_BASE: Dict[str, Dict] = {
    "Dog": {
        "general": (
            "Dogs need at least 2 walks per day (morning and evening), "
            "feeding twice a day (morning and evening), and a play or training session."
        ),
        "puppy": "Puppies under 2 years need shorter walks (15–20 min) but more frequently.",
        "senior": "Senior dogs (8+ years) need gentler, shorter walks and extra rest.",
        "recommended_tasks": ["Morning Walk", "Evening Walk", "Breakfast", "Dinner"],
        "optional_tasks":    ["Grooming", "Training Session", "Playtime"],
    },
    "Cat": {
        "general": (
            "Cats need feeding twice a day, daily litter box cleaning, "
            "and at least one interactive play session per day."
        ),
        "recommended_tasks": ["Breakfast", "Dinner", "Litter Cleaning", "Playtime"],
        "optional_tasks":    ["Brushing", "Health Check"],
    },
    "Other": {
        "general": "Provide species-appropriate feeding, enrichment, and hygiene care daily.",
        "recommended_tasks": ["Morning Feed", "Evening Feed", "Enrichment"],
        "optional_tasks":    ["Cleaning", "Health Check"],
    },
}

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
_log_buffer: List[Dict[str, Any]] = []


def log_pipeline_step(step: str, data: Any) -> None:
    """Append a named step to the in-memory buffer and write to logs/pipeline.log."""
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "step": step,
        "data": data,
    }
    _log_buffer.append(entry)

    os.makedirs("logs", exist_ok=True)
    with open("logs/pipeline.log", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def get_log_buffer() -> List[Dict[str, Any]]:
    """Return a copy of the in-memory log (safe to read from the UI)."""
    return list(_log_buffer)


def clear_log_buffer() -> None:
    """Clear the in-memory log at the start of each pipeline run."""
    _log_buffer.clear()


# ---------------------------------------------------------------------------
# 1. Retriever
# ---------------------------------------------------------------------------
def retrieve_context(pets: List[Pet]) -> str:
    """
    Pull pet care guidelines from the local knowledge base.

    Returns a formatted string that gets injected into the AI planner prompt.
    This is the retriever step — context informs every downstream AI call.
    """
    lines = ["=== Pet Care Guidelines ==="]

    for pet in pets:
        kb = KNOWLEDGE_BASE.get(pet.type, KNOWLEDGE_BASE["Other"])
        lines.append(f"\n{pet.name} ({pet.type}, age {pet.age}):")
        lines.append(f"  • {kb['general']}")

        # Age-specific notes
        if pet.age < 2 and "puppy" in kb:
            lines.append(f"  • Age note: {kb['puppy']}")
        elif pet.age >= 8 and "senior" in kb:
            lines.append(f"  • Age note: {kb['senior']}")

        lines.append(f"  • Recommended tasks: {', '.join(kb['recommended_tasks'])}")
        lines.append(f"  • Optional tasks:    {', '.join(kb['optional_tasks'])}")

    context = "\n".join(lines)
    log_pipeline_step("retrieve_context", context)
    return context


# ---------------------------------------------------------------------------
# 2. AI Planner
# ---------------------------------------------------------------------------
def generate_ai_schedule(
    owner: User,
    context: str,
    feedback: str = "",
    care_instructions: str = "",
) -> Tuple[List[Dict], str]:
    """
    Call Claude to generate an initial pet care schedule (AI Planner step).

    Returns:
        task_list  — list of dicts: {task_name, start_time, duration, priority, pet_name}
        reasoning  — the AI's brief explanation of its decisions
    """
    # Summarise owner constraints for the prompt — show as explicit clock ranges
    constraint_lines = [
        f"{owner.user_schedule.blocked_labels[i]} "
        f"({Schedule._to_time(b_start)} – {Schedule._to_time(b_end)}, "
        f"{b_end - b_start} min blocked)"
        for i, (b_start, b_end) in enumerate(owner.user_schedule.blocked_times)
    ] or ["None — full window is available"]

    window = (
        f"{Schedule._to_time(owner.user_schedule.day_start)} "
        f"to {Schedule._to_time(owner.user_schedule.day_end)}"
    )

    pet_lines = "\n".join(
        f"  - {p.name}: {p.type}, age {p.age}" for p in owner.user_pets
    )

    feedback_block = (
        f"\nOwner feedback to incorporate:\n{feedback}\n" if feedback else ""
    )
    instructions_block = (
        f"\nOwner's care instructions (MUST be followed):\n{care_instructions}\n"
        if care_instructions else ""
    )

    prompt = f"""You are a pet care scheduling assistant for PawPal+.

Owner: {owner.name}
Available schedule window: {window}
BLOCKED — do NOT place any task overlapping these windows: {', '.join(constraint_lines)}
IMPORTANT: Every task's start_time + duration must fall entirely outside the blocked windows.

Pets:
{pet_lines}

{context}
{instructions_block}
{feedback_block}
Generate a realistic daily pet care schedule. Return ONLY valid JSON — no extra text.

Required JSON format:
{{
  "tasks": [
    {{
      "task_name": "Morning Walk",
      "start_time": "7:00am",
      "duration": 30,
      "priority": 5,
      "pet_name": "Buddy"
    }}
  ],
  "reasoning": "One or two sentences explaining your scheduling decisions."
}}

Rules:
- Only schedule tasks inside the available window
- Never schedule during blocked times
- Use 12-hour format for start_time (e.g. "7:00am", "6:30pm")
- duration is an integer number of minutes
- priority is an integer 1–5 (5 = highest)
- Every pet must have at least 2 tasks
- Space tasks realistically throughout the day"""

    log_pipeline_step("planner_prompt", prompt)

    client = Groq()
    raw = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    ).choices[0].message.content
    log_pipeline_step("planner_raw_output", raw)

    data = _extract_json(raw)
    if data is None:
        log_pipeline_step("planner_parse_error", raw[:300])
        return [], f"Could not parse AI response. Raw output logged."

    task_list = data.get("tasks", [])
    reasoning  = data.get("reasoning", "")
    log_pipeline_step("planner_output", {"tasks": task_list, "reasoning": reasoning})
    return task_list, reasoning


# ---------------------------------------------------------------------------
# 3. AI Evaluator
# ---------------------------------------------------------------------------
def evaluate_schedule(
    task_list: List[Dict],
    owner: User,
    context: str,
) -> Tuple[str, List[str]]:
    """
    Ask Claude to evaluate the generated schedule (AI Evaluator step).

    Returns:
        status  — "approved" or "needs_revision"
        issues  — list of specific actionable issues (empty when approved)
    """
    constraint_lines = [
        f"{owner.user_schedule.blocked_labels[i]}: "
        f"{Schedule._to_time(b_start)} – {Schedule._to_time(b_end)}"
        for i, (b_start, b_end) in enumerate(owner.user_schedule.blocked_times)
    ] or ["None"]

    schedule_text = "\n".join(
        f"  - {t.get('pet_name', '?')}: {t.get('task_name')} "
        f"at {t.get('start_time')} ({t.get('duration')} min, priority {t.get('priority')})"
        for t in task_list
    ) or "  (empty schedule)"

    prompt = f"""You are a pet care schedule reviewer for PawPal+.

Owner constraints (tasks must NOT fall here): {', '.join(constraint_lines)}

{context}

Proposed schedule:
{schedule_text}

Review for:
1. Realism — are times reasonable and tasks spaced sensibly?
2. Consistency — does each pet get appropriate care for their species and age?
3. Owner fit — no tasks overlap with blocked times
4. Pet care appropriateness — coverage matches guidelines above

Return ONLY valid JSON:
{{
  "status": "approved",
  "issues": []
}}

or

{{
  "status": "needs_revision",
  "issues": ["specific issue 1", "specific issue 2"]
}}

If approved, "issues" must be an empty list.
If needs_revision, list concrete, actionable problems."""

    log_pipeline_step("evaluator_prompt", prompt)

    client = Groq()
    raw = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    ).choices[0].message.content
    log_pipeline_step("evaluator_raw_output", raw)

    data = _extract_json(raw)
    if data is None:
        log_pipeline_step("evaluator_parse_error", raw[:300])
        status = "approved"   # can't parse → don't block on evaluation
        issues = []
    else:
        status = data.get("status", "needs_revision")
        issues = data.get("issues", [])

    log_pipeline_step("evaluator_result", {"status": status, "issues": issues})
    return status, issues


# ---------------------------------------------------------------------------
# 4. Refinement Loop — orchestrates the full pipeline
# ---------------------------------------------------------------------------
def run_pipeline(
    owner: User,
    max_iterations: int = 3,
    human_feedback: str = "",
    care_instructions: str = "",
) -> Tuple[List[Dict], List[Dict], str]:
    """
    Run the full AI pipeline: retrieve → plan → evaluate → refine if needed.

    Returns:
        final_task_list  — validated (or best-attempt) list of task dicts
        log_entries      — in-memory log for display in the UI
        final_reasoning  — AI's explanation of the final schedule
    """
    clear_log_buffer()
    log_pipeline_step("pipeline_start", {
        "owner": owner.name,
        "pets": [f"{p.name} ({p.type}, age {p.age})" for p in owner.user_pets],
        "care_instructions": care_instructions or "(none)",
        "human_feedback": human_feedback or "(none)",
    })

    # Step 1 — Retrieve context once; reused across all iterations
    context = retrieve_context(owner.user_pets)

    feedback = human_feedback   # carries evaluator issues forward each iteration
    task_list: List[Dict] = []
    reasoning: str = ""

    for iteration in range(1, max_iterations + 1):
        log_pipeline_step("iteration_start", f"Iteration {iteration} of {max_iterations}")

        # Step 2 — Planner
        task_list, reasoning = generate_ai_schedule(
            owner, context, feedback, care_instructions
        )

        if not task_list:
            log_pipeline_step("iteration_error", "Planner returned no tasks — stopping.")
            break

        # Step 3 — Evaluator
        status, issues = evaluate_schedule(task_list, owner, context)

        if status == "approved":
            log_pipeline_step(
                "pipeline_approved",
                f"Schedule approved on iteration {iteration}."
            )
            break

        # Step 4 — Build feedback for next iteration
        feedback = "Please fix these issues:\n" + "\n".join(f"  - {i}" for i in issues)
        log_pipeline_step("refinement_feedback", feedback)

        if iteration == max_iterations:
            log_pipeline_step(
                "pipeline_max_iterations",
                f"Reached max iterations ({max_iterations}). Using best result."
            )

    log_pipeline_step("pipeline_complete", {
        "final_task_count": len(task_list),
        "reasoning": reasoning,
    })

    return task_list, get_log_buffer(), reasoning


# ---------------------------------------------------------------------------
# 5. Apply AI output → existing Schedule structure
# ---------------------------------------------------------------------------

_MIN_TASK_DURATION = 10  # never trim a task below this many minutes


def _find_free_slot(
    schedule: Schedule,
    desired_start: int,
    duration: int,
) -> Tuple[int, int]:
    """
    Find the best (start, duration) pair for a task near desired_start.

    Tries in order:
      1. Exact time + original duration
      2. Exact time + trimmed duration (5-min steps down to _MIN_TASK_DURATION)
      3. Nearby slots (±2 h in 15-min steps, closest first) + original duration
      4. Nearby slots + trimmed duration

    Returns (-1, -1) if no slot can be found.
    """
    day_start = schedule.day_start
    day_end   = schedule.day_end

    # Build a search order: offsets sorted by absolute distance from desired_start
    offsets = sorted(range(-120, 121, 15), key=abs)

    # Durations to try: start from original (or minimum if AI gave a very short
    # duration), then step down in 5-min increments to the minimum.
    start_d   = max(duration, _MIN_TASK_DURATION)
    durations = list(range(start_d, _MIN_TASK_DURATION - 1, -5))
    if not durations:                        # safety net
        durations = [_MIN_TASK_DURATION]

    for d in durations:
        for offset in offsets:
            candidate = desired_start + offset
            if candidate < day_start or candidate + d > day_end:
                continue
            if schedule._slot_is_free(candidate, d):
                return candidate, d

    return -1, -1


def apply_ai_schedule(owner: User, task_list: List[Dict]) -> List[str]:
    """
    Place AI-generated tasks into owner.user_schedule.

    For each task:
    - Try the AI's suggested time first.
    - If blocked or overlapping, find the nearest free slot automatically.
    - If the task can't fit at full duration, trim it until it fits.
    - If no slot exists at all, skip with a warning.

    Writes directly into Schedule._placed so sort_by_time() and the existing
    timeline UI render the result with no changes needed in app.py.

    Returns a list of info/warning strings for tasks that were moved, trimmed,
    or skipped.
    """
    pet_map  = {p.name.lower(): p for p in owner.user_pets}
    warnings: List[str] = []

    # Clear any previously generated schedule
    owner.user_schedule._placed   = {}
    owner.user_schedule._unplaced = []
    owner.user_schedule.tasks     = []

    for item in task_list:
        pet_name = item.get("pet_name", "")
        pet      = pet_map.get(pet_name.lower())

        if pet is None:
            msg = f"Unknown pet '{pet_name}' — '{item.get('task_name')}' skipped."
            warnings.append(msg)
            log_pipeline_step("apply_warning", msg)
            continue

        try:
            original_duration = int(item["duration"])
            task = Task(
                task_name=item["task_name"],
                duration=original_duration,
                priority=int(item["priority"]),
                pet_id=pet.id,
            )
            desired_start = Schedule._parse_time(item["start_time"])
        except (ValueError, KeyError) as exc:
            msg = f"Could not parse '{item.get('task_name')}': {exc}"
            warnings.append(msg)
            log_pipeline_step("apply_error", msg)
            continue

        actual_start, actual_duration = _find_free_slot(
            owner.user_schedule, desired_start, original_duration
        )

        if actual_start == -1:
            msg = (
                f"'{task.task_name}' could not be placed — "
                "no free slot found in the schedule window."
            )
            warnings.append(msg)
            log_pipeline_step("apply_unplaced", msg)
            continue

        # Record adjustments so the user knows what changed
        if actual_start != desired_start:
            warnings.append(
                f"'{task.task_name}' moved from {Schedule._to_time(desired_start)} "
                f"to {Schedule._to_time(actual_start)} to avoid a conflict."
            )
        if actual_duration != original_duration:
            warnings.append(
                f"'{task.task_name}' trimmed from {original_duration} min "
                f"to {actual_duration} min to fit the available slot."
            )

        task.duration = actual_duration
        owner.user_schedule._placed[actual_start] = task

    log_pipeline_step("apply_schedule", {
        "placed":   len(owner.user_schedule._placed),
        "warnings": warnings,
    })
    return warnings


# ---------------------------------------------------------------------------
# 6. Reliability Tests — no API calls, logic-only
# ---------------------------------------------------------------------------
def run_reliability_tests() -> List[Dict]:
    """
    Run predefined tests against pipeline logic components.

    Does NOT call the Anthropic API.
    Returns a list of result dicts: {test, passed, detail}.
    """
    results: List[Dict] = []

    def record(name: str, passed: bool, detail: str = "") -> None:
        results.append({"test": name, "passed": passed, "detail": detail})

    # ── Test 1: retrieve_context includes the pet's name ──────────────────
    pets = [Pet(id=1, name="Buddy", type="Dog", age=3)]
    ctx = retrieve_context(pets)
    record(
        "retrieve_context_includes_pet_name",
        "Buddy" in ctx,
        ctx[:100],
    )

    # ── Test 2: retrieve_context includes Dog guidelines ──────────────────
    record(
        "retrieve_context_dog_guidelines",
        "Walk" in ctx and "Dog" in ctx,
        ctx[:100],
    )

    # ── Test 3: puppy age note appears for age < 2 ────────────────────────
    pups = [Pet(id=2, name="Pup", type="Dog", age=1)]
    ctx_pup = retrieve_context(pups)
    record(
        "retrieve_context_puppy_note",
        "pupp" in ctx_pup.lower(),   # matches "Puppies" or "puppy"
        ctx_pup[:150],
    )

    # ── Test 4: senior age note appears for age >= 8 ─────────────────────
    seniors = [Pet(id=3, name="Old", type="Dog", age=9)]
    ctx_senior = retrieve_context(seniors)
    record(
        "retrieve_context_senior_note",
        "senior" in ctx_senior.lower(),
        ctx_senior[:150],
    )

    # ── Test 5: unknown pet type falls back gracefully ───────────────────
    others = [Pet(id=4, name="Tweety", type="Bird", age=1)]
    ctx_other = retrieve_context(others)
    record(
        "retrieve_context_unknown_type_fallback",
        len(ctx_other) > 0 and "Tweety" in ctx_other,
        ctx_other[:100],
    )

    # ── Test 6: apply_ai_schedule places valid tasks ──────────────────────
    test_owner = User(id=99, name="TestOwner")
    test_pet = Pet(id=1, name="Rex", type="Dog", age=4)
    test_owner.add_pet(test_pet)
    fake_tasks = [
        {"task_name": "Morning Walk", "start_time": "7:00am",
         "duration": 30, "priority": 5, "pet_name": "Rex"},
        {"task_name": "Dinner",       "start_time": "6:00pm",
         "duration": 15, "priority": 4, "pet_name": "Rex"},
    ]
    warns = apply_ai_schedule(test_owner, fake_tasks)
    placed_count = len(test_owner.user_schedule._placed)
    record(
        "apply_ai_schedule_places_valid_tasks",
        placed_count == 2 and warns == [],
        f"Placed: {placed_count}, warnings: {warns}",
    )

    # ── Test 7: apply_ai_schedule skips tasks for unknown pets ────────────
    test_owner2 = User(id=100, name="Owner2")
    test_owner2.add_pet(Pet(id=1, name="Luna", type="Cat", age=2))
    bad_tasks = [
        {"task_name": "Walk", "start_time": "9:00am",
         "duration": 30, "priority": 3, "pet_name": "GhostPet"},
    ]
    warns2 = apply_ai_schedule(test_owner2, bad_tasks)
    record(
        "apply_ai_schedule_skips_unknown_pet",
        len(test_owner2.user_schedule._placed) == 0 and len(warns2) == 1,
        f"Warnings: {warns2}",
    )

    # ── Test 8: apply_ai_schedule clears previous schedule ───────────────
    test_owner3 = User(id=101, name="Owner3")
    test_owner3.add_pet(Pet(id=1, name="Max", type="Dog", age=5))
    old = Task(task_name="Old Task", duration=20, priority=3, pet_id=1)
    test_owner3.user_schedule.add_task_at(old, "8:00am")
    apply_ai_schedule(test_owner3, [])   # empty list clears everything
    record(
        "apply_ai_schedule_clears_previous",
        len(test_owner3.user_schedule._placed) == 0,
        f"Placed after empty apply: {len(test_owner3.user_schedule._placed)}",
    )

    # ── Test 9: log_pipeline_step appends to buffer ───────────────────────
    clear_log_buffer()
    log_pipeline_step("test_step", {"key": "value"})
    buf = get_log_buffer()
    record(
        "log_pipeline_step_appends",
        len(buf) == 1 and buf[0]["step"] == "test_step",
        str(buf[0]),
    )

    # ── Test 10: clear_log_buffer empties the buffer ──────────────────────
    clear_log_buffer()
    record(
        "clear_log_buffer_empties",
        get_log_buffer() == [],
        "Buffer is empty after clear.",
    )

    return results
