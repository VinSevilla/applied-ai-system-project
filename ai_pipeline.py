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
        f.write(json.dumps(entry, default=str) + "\n")


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
# 2a. Care instruction parser — runs in Python before any AI call
# ---------------------------------------------------------------------------
import re as _re

# Task keywords that can reasonably be shared across multiple pets at once
_COMBINABLE = {"feed", "walk", "play", "groom", "brush", "exercise", "train", "bathe"}

# Keywords that signal the owner wants tasks done individually, not shared
_SEPARATE_KW = {"separate", "separately", "individually", "one by one", "one at a time",
                "their own", "not together", "apart", "each one"}

# Optional spell checker — silently disabled if pyspellchecker is not installed
try:
    from spellchecker import SpellChecker as _SpellChecker
    _spell = _SpellChecker()
    _SPELL_OK = True
except ImportError:
    _SPELL_OK = False


def _spell_fix(text: str) -> str:
    """Best-effort typo correction on a short phrase (skips short/numeric tokens)."""
    if not _SPELL_OK or not text.strip():
        return text
    words = text.split()
    out = []
    for word in words:
        alpha = _re.sub(r"[^a-zA-Z]", "", word)
        if len(alpha) < 4:          # skip short words — too risky to "correct"
            out.append(word)
            continue
        correction = _spell.correction(alpha.lower())
        if correction and correction != alpha.lower():
            fixed = (correction[0].upper() + correction[1:]) if alpha[0].isupper() else correction
            out.append(word.replace(alpha, fixed, 1))
        else:
            out.append(word)
    return " ".join(out)

# Realistic duration suggestions (minutes) keyed by keyword in task name
_DURATION_HINTS: Dict[str, str] = {
    "feed":        "5–10",
    "meal":        "5–10",
    "breakfast":   "5–10",
    "dinner":      "5–10",
    "lunch":       "5–10",
    "walk":        "15–20",
    "exercise":    "15–30",
    "train":       "10–15",
    "play":        "10–15",
    "groom":       "15–30",
    "brush":       "10–15",
    "bathe":       "15–20",
    "medicine":    "5",
    "medication":  "5",
    "litter":      "5–10",
    "clean":       "5–10",
    "health":      "10–15",
    "enrichment":  "10–15",
}

# Frequency phrases → how many task instances to create
_FREQ_RE = _re.compile(
    r"\b(once|twice|two\s+times?|three\s+times?|four\s+times?|[2-4]\s*times?)"
    r"(?:\s+(?:a\s+)?day|\s+daily)?\b",
    _re.IGNORECASE,
)

# Default spread times when user says "twice/three times a day" with no explicit time hint
_DEFAULT_SPREAD_TIMES: Dict[int, List[str]] = {
    2: ["7:00am", "6:00pm"],
    3: ["7:00am", "12:00pm", "6:00pm"],
    4: ["7:00am", "10:00am", "2:00pm", "6:00pm"],
}


def _parse_care_instructions(text: str, pets: List[Pet]) -> List[Dict]:
    """
    Convert free-text care instructions into a structured task list.

    Rules applied in Python (not AI) so results are deterministic:
    - Each comma/semicolon/newline-separated phrase becomes one task intent.
    - If the phrase explicitly names one pet, that task belongs to that pet only.
    - If the phrase names multiple pets, or names none (implicit "all pets"), AND
      the action is combinable (walk, feed, play…), produce ONE shared entry
      so both pets are done in a single time slot.
    - If the action is not combinable (medicine, vet…), duplicate per pet.
    - Time hints like "at 8am" are extracted and passed to the AI as guidance.

    Returns a list of task-intent dicts:
        task_name, pet_label (display string), pet_name (primary for Task.pet_id),
        all_pets (list of Pet objects), time_hint (str|None), combined (bool)
    """
    if not text.strip():
        return []

    pet_lookup = {p.name.lower(): p for p in pets}

    raw_items = [s.strip() for s in _re.split(r"[,;\n]+", text) if s.strip()]
    intents: List[Dict] = []

    for phrase in raw_items:
        phrase_lower = phrase.lower()

        # Extract time hint ("at 8am", "at 6:30pm")
        time_match = _re.search(
            r"\bat\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))", phrase, _re.IGNORECASE
        )
        time_hint = (
            time_match.group(1).replace(" ", "").lower() if time_match else None
        )

        # Detect frequency ("twice a day" → 2, "3 times" → 3, etc.)
        freq_m = _FREQ_RE.search(phrase)
        frequency = 1
        if freq_m:
            tok = _re.sub(r"\s+", " ", freq_m.group(1).lower().strip())
            if tok in ("twice", "two times", "two time", "2 times", "2 time"):
                frequency = 2
            elif tok in ("three times", "three time", "3 times", "3 time"):
                frequency = 3
            elif tok in ("four times", "four time", "4 times", "4 time"):
                frequency = 4

        # Which pets are mentioned explicitly?
        named_pets = [p for key, p in pet_lookup.items() if key in phrase_lower]

        # "both" / "all" / no explicit name → every pet
        use_all = (
            not named_pets
            or any(kw in phrase_lower for kw in ("both", "all", "every", "each"))
        )
        target_pets = list(pets) if use_all else named_pets

        # Is this a combinable action?
        is_combinable = any(kw in phrase_lower for kw in _COMBINABLE)

        # Does the owner explicitly want tasks done separately (overrides combining)?
        is_separate = any(kw in phrase_lower for kw in _SEPARATE_KW)

        # Build a clean task name: strip pet names, time hints, filler words
        task_name = phrase
        for p in pets:
            task_name = _re.sub(
                rf"\b{_re.escape(p.name)}\b", "", task_name, flags=_re.IGNORECASE
            )
        task_name = _re.sub(
            r"\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)", "", task_name, flags=_re.IGNORECASE
        )
        task_name = _re.sub(r"\b(both|all|every|each|and|or|add|also|please)\b", "", task_name, flags=_re.IGNORECASE)
        # Strip "don't combine" signal words from the visible task name
        sep_pattern = "|".join(_re.escape(kw) for kw in _SEPARATE_KW)
        task_name = _re.sub(sep_pattern, "", task_name, flags=_re.IGNORECASE)
        task_name = _FREQ_RE.sub("", task_name)
        task_name = _re.sub(r"\b(?:daily|a\s+day|per\s+day|every\s+day)\b", "", task_name, flags=_re.IGNORECASE)
        task_name = _re.sub(r"\s+", " ", task_name).strip().strip(",").strip()
        task_name = _spell_fix(task_name)
        if task_name:
            task_name = task_name[0].upper() + task_name[1:]
        if not task_name:
            task_name = phrase.strip().capitalize()

        # Build per-occurrence time hints.
        # time_hint_is_user=True means the owner typed an explicit time — treat as required.
        # time_hint_is_user=False means a system-generated spread default — treat as suggestion.
        spread = _DEFAULT_SPREAD_TIMES.get(frequency, [])
        def _occ_hint(occ: int):
            if occ == 0 and time_hint:
                return time_hint, True        # user-provided → required
            t = spread[occ] if occ < len(spread) else None
            return t, False                   # system default → suggested only

        if len(target_pets) > 1 and is_combinable and not is_separate:
            pet_label = " & ".join(p.name for p in target_pets)
            for occ in range(frequency):
                hint, hint_is_user = _occ_hint(occ)
                intents.append({
                    "task_name":       task_name,
                    "pet_label":       pet_label,
                    "pet_name":        pet_label,
                    "all_pets":        target_pets,
                    "time_hint":       hint,
                    "time_hint_is_user": hint_is_user,
                    "combined":        True,
                })
        else:
            for p in target_pets:
                for occ in range(frequency):
                    hint, hint_is_user = _occ_hint(occ)
                    intents.append({
                        "task_name":       task_name,
                        "pet_label":       p.name,
                        "pet_name":        p.name,
                        "all_pets":        [p],
                        "time_hint":       hint,
                        "time_hint_is_user": hint_is_user,
                        "combined":        False,
                    })

    log_pipeline_step("parsed_intents", [
        f"{i['task_name']} → {i['pet_label']}" + (f" @ {i['time_hint']}" if i['time_hint'] else "")
        for i in intents
    ])
    return intents


# ---------------------------------------------------------------------------
# 2b. AI Planner — assigns timing only; task list is fixed by the parser above
# ---------------------------------------------------------------------------
def generate_ai_schedule(
    owner: User,
    context: str,
    feedback: str = "",
    care_instructions: str = "",
    human_feedback: str = "",
) -> Tuple[List[Dict], str]:
    """
    AI Planner step.

    The task list is determined by _parse_care_instructions (Python, deterministic).
    The AI's only job is to assign a sensible start_time and duration to each task.
    It cannot add or remove tasks.

    Returns:
        task_list  — list of dicts: {task_name, start_time, duration, priority, pet_name}
        reasoning  — brief explanation
    """
    if not care_instructions or not care_instructions.strip():
        log_pipeline_step("planner_skip", "No care instructions — returning empty schedule.")
        return [], "No care instructions provided."

    # Step 1: Parse care instructions into fixed task intents (Python, not AI)
    intents = _parse_care_instructions(care_instructions, owner.user_pets)

    # If the human provided new care tasks in their feedback, parse and append them.
    # Only human_feedback is parsed here — evaluator refinement issues must never be
    # treated as new task requests (they'd produce garbage task names from issue text).
    if human_feedback:
        _ADD_ACTIONS = {
            "feed", "walk", "play", "groom", "brush", "exercise", "train",
            "bathe", "bath", "give", "wash", "clean", "litter", "medicate",
        }
        # Strip request-framing words so "add give mochi a bath" → "give mochi a bath"
        fb_clean = _re.sub(
            r"\b(add|also|please|can you|could you|i want|i need|i'?d like)\b",
            "", human_feedback, flags=_re.IGNORECASE,
        )
        fb_clean = _re.sub(r"\s+", " ", fb_clean).strip()
        # Split on "." "and" etc. so "Move feed to 9pm. Give mochi a bath" → 2 fragments
        fb_parts = [s.strip() for s in _re.split(r"[,;.\n]|\s+and\s+", fb_clean) if s.strip()]
        fb_intents = _parse_care_instructions("\n".join(fb_parts), owner.user_pets)
        for fi in fb_intents:
            first_word = fi["task_name"].lower().split()[0] if fi["task_name"].split() else ""
            if first_word in _ADD_ACTIONS:
                intents.append(fi)
                log_pipeline_step("feedback_added_task", f"{fi['task_name']} → {fi['pet_label']}")

    if not intents:
        return [], "Could not parse any tasks from care instructions."

    def _dur_hint(task_name: str) -> str:
        lower = task_name.lower()
        for kw, hint in _DURATION_HINTS.items():
            if kw in lower:
                return f", suggested duration: {hint} min"
        return ""

    # Step 2: Build a numbered task list for the AI prompt
    def _fmt_intent(i: int, t: Dict) -> str:
        line = f"  {i+1}. {t['task_name']} — for {t['pet_label']}"
        if t.get("time_hint"):
            label = "REQUIRED time" if t.get("time_hint_is_user") else "suggested time"
            line += f" ({label}: {t['time_hint']})"
        line += _dur_hint(t["task_name"])
        return line

    # Stagger suggested (non-user) morning/evening times so tasks don't pile up.
    # e.g. two morning tasks: 7:00am, 7:45am — two evening tasks: 5:00pm, 5:45pm
    morning_step = 0
    evening_step = 0
    prompt_intents = []
    for t in intents:
        pt = dict(t)
        if not t.get("time_hint_is_user") and t.get("time_hint"):
            try:
                base = Schedule._parse_time(t["time_hint"])
                if base < 12 * 60:                  # morning
                    pt["time_hint"] = Schedule._to_time(7 * 60 + morning_step)
                    morning_step += 45
                else:                               # evening
                    pt["time_hint"] = Schedule._to_time(17 * 60 + evening_step)
                    evening_step += 45
            except Exception:
                pass
        prompt_intents.append(pt)

    numbered_tasks = "\n".join(_fmt_intent(i, t) for i, t in enumerate(prompt_intents))

    window = (
        f"{Schedule._to_time(owner.user_schedule.day_start)} "
        f"to {Schedule._to_time(owner.user_schedule.day_end)}"
    )

    blocked_str = ", ".join(
        f"{owner.user_schedule.blocked_labels[i]} "
        f"({Schedule._to_time(b_start)}–{Schedule._to_time(b_end)})"
        for i, (b_start, b_end) in enumerate(owner.user_schedule.blocked_times)
    ) or "none"

    feedback_block = (
        f"\n⚠️ USER CHANGE REQUESTS — apply these exactly before anything else:\n{feedback}\n"
        if feedback else ""
    )

    prompt = f"""You are a pet care scheduling assistant.
{feedback_block}
Schedule window: {window}
Blocked times — do NOT overlap these: {blocked_str}

Here are exactly {len(intents)} tasks to schedule. Do NOT add or remove any.
{numbered_tasks}

{context}

Return ONLY valid JSON — no other text:
{{
  "schedule": [
    {{"index": 1, "start_time": "7:00am", "duration": 30, "priority": 5}},
    {{"index": 2, "start_time": "8:00am", "duration": 10, "priority": 4}}
  ],
  "reasoning": "One sentence."
}}

Rules:
- You MUST return exactly {len(intents)} entries in "schedule" — one for every index from 1 to {len(intents)}. Missing any index will cause that task to be dropped entirely.
- start_time must be inside the schedule window and outside all blocked times.
- Use 12-hour format: "7:00am", "6:30pm".
- duration is integer minutes (reasonable for the task type).
- priority is integer 1–5 (5 = highest).
- REQUIRED times must be used exactly (unless blocked). Suggested times are defaults — override them freely, especially when user change requests say otherwise.
- If user change requests are listed above, they override everything else — apply them first before considering any suggested times.
- Use realistic durations: feeding 5–10 min, walking 15–20 min, grooming 15–30 min, medicine 5 min, play 10–15 min.
- Spread tasks out: leave at least 30 minutes between any two tasks that involve the same pets.
- Spread repeated tasks across the day: first occurrence in the morning, last occurrence in the evening."""

    log_pipeline_step("planner_prompt", prompt)

    client = Groq()
    raw = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    ).choices[0].message.content
    log_pipeline_step("planner_raw_output", raw)

    data = _extract_json(raw)
    if data is None:
        log_pipeline_step("planner_parse_error", raw[:400])
        return [], "Could not parse AI response."

    schedule_entries = data.get("schedule", [])
    reasoning        = data.get("reasoning", "")

    # Step 3: Merge AI timing back onto the pre-parsed intents
    timing_by_index = {int(e["index"]): e for e in schedule_entries if "index" in e}
    task_list: List[Dict] = []

    def _fallback_duration(task_name: str) -> int:
        """Return a reasonable default duration (minutes) from the task name."""
        lower = task_name.lower()
        for kw, hint in _DURATION_HINTS.items():
            if kw in lower:
                nums = _re.findall(r'\d+', hint)
                if nums:
                    return sum(int(n) for n in nums) // len(nums)
        return 15

    for i, intent in enumerate(intents):
        timing = timing_by_index.get(i + 1)
        if not timing:
            log_pipeline_step("planner_missing_index",
                              f"No timing for index {i+1} ({intent['task_name']}) — using fallback time")
            # Use the intent's time_hint if present; otherwise a safe mid-morning default
            fallback_start = intent.get("time_hint") or "10:00am"
            timing = {
                "start_time": fallback_start,
                "duration":   _fallback_duration(intent["task_name"]),
                "priority":   3,
            }
        task_list.append({
            "task_name":  intent["task_name"],
            "start_time": timing.get("start_time", "9:00am"),
            "duration":   int(timing.get("duration", 30)),
            "priority":   int(timing.get("priority", 3)),
            "pet_name":   intent["pet_name"],
            "all_pets":   intent["all_pets"],
            "combined":   intent["combined"],
        })

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

Proposed schedule:
{schedule_text}

Review ONLY for these two things:
1. Owner fit — do any tasks overlap with the blocked times listed above?
2. Realism — are times reasonable and tasks spaced sensibly (no two tasks for the same pet overlapping)?

IMPORTANT: Do NOT flag the schedule for missing tasks or insufficient coverage.
The owner chose exactly which tasks to include — do not suggest adding more.
An empty schedule is valid if the owner requested no tasks.

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
If needs_revision, list only concrete scheduling conflicts (blocked-time overlaps or same-pet time overlaps)."""

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
            owner, context, feedback, care_instructions,
            human_feedback=human_feedback,
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


# ---------------------------------------------------------------------------
# Schedule optimization — group same-task / same-time entries across pets
# ---------------------------------------------------------------------------

def _merge_task_list(
    task_list: List[Dict],
    pet_map: Dict[str, "Pet"],
) -> Tuple[List[Dict], List[str]]:
    """
    Pre-process the raw AI task list before placement.

    When multiple pets have the SAME task_name at the SAME start_time, merge
    them into a single entry whose pet_name is a comma-separated list of all
    the pets involved (e.g. "Buddy, Max").  The merged entry uses the highest
    priority and the longest duration among the group.

    Pets with the same task at DIFFERENT times are left as separate entries.

    Returns:
        merged_list — deduplicated / merged task dicts ready for placement
        notes       — human-readable strings describing every merge that happened
    """
    from collections import defaultdict

    # Key: (normalised task_name, start_time string) → list of raw items
    groups: Dict[Tuple[str, str], List[Dict]] = defaultdict(list)
    for item in task_list:
        key = (item.get("task_name", "").strip().lower(), item.get("start_time", "").strip().lower())
        groups[key].append(item)

    merged_list: List[Dict] = []
    notes: List[str] = []

    for (task_key, time_key), items in groups.items():
        if len(items) == 1:
            merged_list.append(items[0])
            continue

        # Collect valid pet names (skip unknowns — they'll be warned later)
        pet_names = []
        for it in items:
            pn = it.get("pet_name", "").strip()
            if pn.lower() in pet_map:
                if pn not in pet_names:
                    pet_names.append(pn)

        if not pet_names:
            # All pets unknown — keep originals so they each get a "skipped" warning
            merged_list.extend(items)
            continue

        best_priority = max(int(it.get("priority", 3)) for it in items)
        best_duration = max(int(it.get("duration", 30)) for it in items)
        canonical_name = items[0].get("task_name", "").strip()
        start_time     = items[0].get("start_time", "").strip()

        merged_entry = {
            "task_name":  canonical_name,
            "start_time": start_time,
            "duration":   best_duration,
            "priority":   best_priority,
            "pet_name":   ", ".join(pet_names),   # e.g. "Buddy, Max"
        }
        merged_list.append(merged_entry)

        note = (
            f"Optimized: '{canonical_name}' at {start_time} merged for "
            f"{', '.join(pet_names)} into one shared slot."
        )
        notes.append(note)
        log_pipeline_step("schedule_merge", note)

    return merged_list, notes


_MIN_GAP_BETWEEN_TASKS = 30   # minimum minutes between tasks for the same pet group


def apply_ai_schedule(owner: User, task_list: List[Dict]) -> List[str]:
    """
    Place the AI-timed task list into owner.user_schedule.

    - Tasks are sorted by their AI-assigned start time before placement.
    - A minimum gap (_MIN_GAP_BETWEEN_TASKS) is enforced between tasks that
      share the same pet group; the desired start is pushed forward if needed.
    - If the slot is blocked/occupied, _find_free_slot finds the nearest free slot.
    - If no slot fits, skip with a warning.

    Returns info/warning strings for moves, trims, or skips.
    """
    pet_map  = {p.name.lower(): p for p in owner.user_pets}
    warnings: List[str] = []

    owner.user_schedule._placed   = {}
    owner.user_schedule._unplaced = []
    owner.user_schedule.tasks     = []

    # Sort tasks by their AI-assigned start so earlier tasks are placed first
    def _desired(item: Dict) -> int:
        try:
            return Schedule._parse_time(item["start_time"])
        except Exception:
            return owner.user_schedule.day_start

    sorted_task_list = sorted(task_list, key=_desired)

    # Ensure repeated tasks (same name + same pet group) are split across the day.
    # If the AI placed both occurrences in the morning (or both in the evening),
    # force one to morning and one to evening so the day is actually covered.
    _DAY_MID = 13 * 60   # 1:00 PM — splits "morning half" from "evening half"
    _MORNING  =  7 * 60  # default anchor when we need to push a task to morning
    _EVENING  = 17 * 60  # default anchor when we need to push a task to evening

    group_map: Dict[tuple, list] = {}
    for item in sorted_task_list:
        try:
            pk = frozenset(p.id for p in (item.get("all_pets") or []))
        except Exception:
            pk = frozenset()
        key = (item.get("task_name", "").lower().strip(), pk)
        group_map.setdefault(key, []).append(item)

    for group in group_map.values():
        if len(group) == 2:
            a, b = group
            ta, tb = _desired(a), _desired(b)
            if ta < _DAY_MID and tb < _DAY_MID:   # both morning → push second to evening
                b["_override_start"] = _EVENING
            elif ta >= _DAY_MID and tb >= _DAY_MID:  # both evening → push first to morning
                a["_override_start"] = _MORNING
        elif len(group) >= 3:
            offsets = [_MORNING, 12 * 60, _EVENING] + [_EVENING + i * 90 for i in range(1, len(group) - 2)]
            for item, offset in zip(group, offsets):
                item["_override_start"] = offset

    # Re-sort after potential override injections
    sorted_task_list = sorted(
        sorted_task_list,
        key=lambda it: it.get("_override_start", _desired(it)),
    )

    # Track the end minute of the last placed task per pet group
    last_end_for: Dict[frozenset, int] = {}

    for item in sorted_task_list:
        # Resolve primary pet (for Task.pet_id)
        all_pets: List[Pet] = item.get("all_pets", [])
        if not all_pets:
            # Fallback: resolve from pet_name string
            raw_names = _re.split(r"[,&]+", item.get("pet_name", ""))
            all_pets = [pet_map[n.strip().lower()] for n in raw_names
                        if n.strip().lower() in pet_map]
        if not all_pets:
            msg = f"Unknown pet(s) '{item.get('pet_name', '?')}' — '{item.get('task_name')}' skipped."
            warnings.append(msg)
            log_pipeline_step("apply_warning", msg)
            continue

        primary_pet  = all_pets[0]
        display_pets = " & ".join(p.name for p in all_pets)

        try:
            original_duration = int(item["duration"])
            task = Task(
                task_name=item["task_name"],
                duration=original_duration,
                priority=int(item["priority"]),
                pet_id=primary_pet.id,
            )
            task._display_pets = display_pets
            desired_start = Schedule._parse_time(item["start_time"])
        except (ValueError, KeyError) as exc:
            msg = f"Could not parse '{item.get('task_name')}': {exc}"
            warnings.append(msg)
            log_pipeline_step("apply_error", msg)
            continue

        # Apply day-split override (morning/evening assignment)
        override = item.pop("_override_start", None)
        if override is not None:
            desired_start = override

        # Enforce minimum gap after the last task for this pet group
        pet_key = frozenset(p.id for p in all_pets)
        last_end = last_end_for.get(pet_key)
        if last_end is not None and desired_start < last_end + _MIN_GAP_BETWEEN_TASKS:
            desired_start = last_end + _MIN_GAP_BETWEEN_TASKS

        actual_start, actual_duration = _find_free_slot(
            owner.user_schedule, desired_start, original_duration
        )

        if actual_start == -1:
            msg = f"'{task.task_name}' could not be placed — no free slot found."
            warnings.append(msg)
            log_pipeline_step("apply_unplaced", msg)
            continue

        if actual_start != Schedule._parse_time(item["start_time"]):
            warnings.append(
                f"'{task.task_name}' moved to {Schedule._to_time(actual_start)} "
                f"(original time adjusted for spacing or conflicts)."
            )
        if actual_duration != original_duration:
            warnings.append(
                f"'{task.task_name}' trimmed to {actual_duration} min "
                f"(from {original_duration} min) to fit the available slot."
            )

        task.duration = actual_duration
        owner.user_schedule._placed[actual_start] = task
        last_end_for[pet_key] = actual_start + actual_duration

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

    # ── Test 11: _merge_task_list merges same-task / same-time entries ────
    merge_owner = User(id=200, name="MergeOwner")
    pet_a = Pet(id=1, name="Buddy", type="Dog", age=3)
    pet_b = Pet(id=2, name="Max",   type="Dog", age=4)
    merge_owner.add_pet(pet_a)
    merge_owner.add_pet(pet_b)
    merge_pet_map = {p.name.lower(): p for p in merge_owner.user_pets}
    same_time_tasks = [
        {"task_name": "Morning Walk", "start_time": "7:00am", "duration": 30, "priority": 5, "pet_name": "Buddy"},
        {"task_name": "Morning Walk", "start_time": "7:00am", "duration": 30, "priority": 4, "pet_name": "Max"},
    ]
    merged, merge_notes = _merge_task_list(same_time_tasks, merge_pet_map)
    record(
        "merge_task_list_combines_same_time",
        len(merged) == 1 and "Buddy" in merged[0]["pet_name"] and "Max" in merged[0]["pet_name"],
        f"Merged entries: {len(merged)}, pet_name: '{merged[0]['pet_name'] if merged else ''}', notes: {merge_notes}",
    )

    # ── Test 12: _merge_task_list keeps different-time entries separate ───
    diff_time_tasks = [
        {"task_name": "Walk", "start_time": "7:00am", "duration": 30, "priority": 5, "pet_name": "Buddy"},
        {"task_name": "Walk", "start_time": "6:00pm", "duration": 30, "priority": 4, "pet_name": "Max"},
    ]
    merged2, _ = _merge_task_list(diff_time_tasks, merge_pet_map)
    record(
        "merge_task_list_keeps_different_times_separate",
        len(merged2) == 2,
        f"Entries after merge: {len(merged2)} (expected 2 — different times stay separate)",
    )

    # ── Test 13: apply_ai_schedule places combined multi-pet task correctly ─
    merge_owner2 = User(id=201, name="MergeOwner2")
    pet_c = Pet(id=1, name="Luna",  type="Cat", age=2)
    pet_d = Pet(id=2, name="Mochi", type="Cat", age=3)
    merge_owner2.add_pet(pet_c)
    merge_owner2.add_pet(pet_d)
    # New architecture: _parse_care_instructions produces ONE entry with all_pets set
    shared_tasks = [
        {
            "task_name": "Breakfast",
            "start_time": "8:00am",
            "duration": 10,
            "priority": 5,
            "pet_name": "Luna & Mochi",
            "all_pets": [pet_c, pet_d],
            "combined": True,
        },
    ]
    warns_merge = apply_ai_schedule(merge_owner2, shared_tasks)
    placed_merge = merge_owner2.user_schedule._placed
    merged_task = list(placed_merge.values())[0] if placed_merge else None
    display_pets = getattr(merged_task, "_display_pets", "") if merged_task else ""
    record(
        "apply_ai_schedule_merges_shared_slot",
        len(placed_merge) == 1 and "Luna" in display_pets and "Mochi" in display_pets,
        f"Placed slots: {len(placed_merge)}, _display_pets: '{display_pets}'",
    )

    return results
