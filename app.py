import os

import streamlit as st

import ai_pipeline
from pawpal_system import Schedule, Task, Pet, User

st.set_page_config(page_title="PawPal+", page_icon="🐾", layout="centered")
st.title("🐾 PawPal+")

# ---------------------------------------------------------------------------
# Session state — initialize once, survives reruns
# ---------------------------------------------------------------------------
if "owner" not in st.session_state:
    st.session_state.owner = None

if "next_pet_id" not in st.session_state:
    st.session_state.next_pet_id = 1

# AI pipeline state
if "ai_task_list" not in st.session_state:
    st.session_state.ai_task_list = None
if "ai_log_buffer" not in st.session_state:
    st.session_state.ai_log_buffer = []
if "ai_reasoning" not in st.session_state:
    st.session_state.ai_reasoning = ""
if "ai_warnings" not in st.session_state:
    st.session_state.ai_warnings = []
if "ai_schedule_applied" not in st.session_state:
    st.session_state.ai_schedule_applied = False

def _time_spinners(label, default_hour, default_minute, default_period, key_prefix):
    """Render hour / minute / AM-PM spinners and return total minutes from midnight."""
    st.markdown(f"**{label}**")
    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        hour = st.number_input("Hour", min_value=1, max_value=12,
                               value=default_hour, step=1, key=f"{key_prefix}_h")
    with c2:
        minute = st.number_input("Min", min_value=0, max_value=59,
                                 value=default_minute, step=1, key=f"{key_prefix}_m",
                                 format="%02d")
    with c3:
        period = st.selectbox("AM/PM", ["AM", "PM"],
                              index=0 if default_period == "AM" else 1,
                              key=f"{key_prefix}_p")

    # Convert to minutes from midnight
    h24 = (0 if hour == 12 else hour) if period == "AM" else (12 if hour == 12 else hour + 12)
    return h24 * 60 + minute

# ---------------------------------------------------------------------------
# Owner setup
# ---------------------------------------------------------------------------
st.subheader("Owner")
owner_name = st.text_input("Your name", value="Jordan")

if st.button("Create / Update Owner"):
    letters_only = ''.join(c for c in owner_name if c.isalpha())
    if not letters_only:
        st.error("Owner name must contain letters only — no numbers or special characters.")
    else:
        clean_owner_name = letters_only[0].upper() + letters_only[1:]
        if st.session_state.owner is None:
            # First time — create a new User and store it in session state
            st.session_state.owner = User(id=1, name=clean_owner_name)
        else:
            # Already exists — just update the name in place
            st.session_state.owner.edit_user_info(clean_owner_name)
        st.success(f"Owner set to: {clean_owner_name}")

owner = st.session_state.owner

if owner is None:
    st.info("Create an owner above to get started.")
    st.stop()  # Don't render anything below until an owner exists

st.divider()

# ---------------------------------------------------------------------------
# Schedule window — let the user pick their active day start/end
# ---------------------------------------------------------------------------
st.subheader("Schedule Window")

col1, col2 = st.columns(2)
with col1:
    sched_start = _time_spinners("Day starts at", default_hour=6,  default_minute=0,  default_period="AM", key_prefix="sched_start")
with col2:
    sched_end   = _time_spinners("Day ends at",   default_hour=11, default_minute=59, default_period="PM", key_prefix="sched_end")

if sched_end <= sched_start:
    st.error("Day end must be after day start.")
else:
    owner.user_schedule.day_start = sched_start
    owner.user_schedule.day_end   = sched_end
    from pawpal_system import Schedule as _S
    st.caption(f"Tasks will be scheduled between {_S._to_time(sched_start)} and {_S._to_time(sched_end)}.")

# ---------------------------------------------------------------------------
# Add a Pet  →  owner.add_pet(Pet(...))
# ---------------------------------------------------------------------------
st.subheader("Add a Pet")

with st.form("add_pet_form"):
    pet_name = st.text_input("Pet name", value="Mochi")
    pet_type = st.selectbox("Species", ["Dog", "Cat", "Other"])
    pet_age  = st.number_input("Age", min_value=0, max_value=30, value=2)
    pet_submitted = st.form_submit_button("Add Pet")

if pet_submitted:
    letters_only = ''.join(c for c in pet_name if c.isalpha())
    if not letters_only:
        st.error("Pet name must contain letters only — no numbers or special characters.")
    else:
        clean_name = letters_only[0].upper() + letters_only[1:]
        new_pet = Pet(
            id=st.session_state.next_pet_id,
            name=clean_name,
            type=pet_type,
            age=int(pet_age)
        )
        owner.add_pet(new_pet)                  # <-- Pet class method
        st.session_state.next_pet_id += 1
        st.success(f"Added {clean_name} the {pet_type}!")

# Show current pets — re-rendered every rerun from session_state.owner.user_pets
if owner.user_pets:
    st.write("**Your pets:**")
    col_headers = st.columns([1, 2, 2, 1, 1])
    for header, label in zip(col_headers, ["#", "Name", "Species", "Age", ""]):
        header.markdown(f"**{label}**")
    for i, p in enumerate(list(owner.user_pets)):
        c0, c1, c2, c3, c4 = st.columns([1, 2, 2, 1, 1])
        c0.write(i + 1)
        c1.write(p.name)
        c2.write(p.type)
        c3.write(p.age)
        if c4.button("✕", key=f"del_pet_{p.id}"):
            owner.user_schedule.tasks = [t for t in owner.user_schedule.tasks if t.pet_id != p.id]
            owner.remove_pet(p.id)
            st.rerun()
else:
    st.info("No pets yet — add one above.")

st.divider()

# ---------------------------------------------------------------------------
# Block off the owner's time  →  owner.add_constraint(...)
# ---------------------------------------------------------------------------
st.subheader("Block Off Your Time")

with st.form("constraint_form"):
    label      = st.text_input("Plan label (e.g. Work, Gym)", value="Work")
    start_time = st.text_input("Start time (e.g. 9:00am)", value="9:00am")
    end_time   = st.text_input("End time   (e.g. 5:00pm)", value="5:00pm")
    constraint_submitted = st.form_submit_button("Add Constraint")

if constraint_submitted:
    try:
        owner.add_constraint(label, start_time, end_time)   # <-- User class method
        st.success(f"Blocked '{label}' from {start_time} to {end_time}.")
    except ValueError as e:
        st.error(str(e))

if owner.user_schedule.blocked_times:
    st.write("**Your constraints:**")
    c_headers = st.columns([3, 2, 2, 1])
    for h, lbl in zip(c_headers, ["Label", "Start", "End", ""]):
        h.markdown(f"**{lbl}**")
    for i, (b_start, b_end) in enumerate(list(owner.user_schedule.blocked_times)):
        c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
        c1.write(owner.user_schedule.blocked_labels[i])
        c2.write(Schedule._to_time(b_start))
        c3.write(Schedule._to_time(b_end))
        if c4.button("✕", key=f"del_constraint_{i}"):
            owner.user_schedule.blocked_times.pop(i)
            owner.user_schedule.blocked_labels.pop(i)
            owner.user_schedule._placed = {}
            owner.user_schedule._unplaced = []
            st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Add a Care Task  →  owner.user_schedule.add_task(Task(...))
# ---------------------------------------------------------------------------
st.subheader("Add a Care Task")

if not owner.user_pets:
    st.warning("Add a pet first before scheduling tasks.")
else:
    pet_map = {p.name: p for p in owner.user_pets}

    with st.form("add_task_form"):
        task_name    = st.text_input("Task name", value="Morning Walk")
        duration     = st.number_input("Duration (minutes)", min_value=1, max_value=240, value=30)
        priority     = st.slider("Priority (1 = low, 5 = high)", min_value=1, max_value=5, value=3)
        selected_pet = st.selectbox("For which pet?", list(pet_map.keys()))
        task_submitted = st.form_submit_button("Add Task")

    if task_submitted:
        pet = pet_map[selected_pet]
        new_task = Task(
            task_name=task_name,
            duration=int(duration),
            priority=priority,
            pet_id=pet.id
        )
        owner.user_schedule.add_task(new_task)    # <-- Schedule class method
        st.success(f"Task '{task_name}' added for {selected_pet}!")

    # Show the unscheduled task pool
    if owner.user_schedule.tasks:
        id_to_name = {p.id: p.name for p in owner.user_pets}
        st.write("**Task pool (not yet scheduled):**")
        t_headers = st.columns([3, 2, 1, 2, 1])
        for header, label in zip(t_headers, ["Task", "Duration (min)", "Priority", "Pet", ""]):
            header.markdown(f"**{label}**")
        for i, t in enumerate(list(owner.user_schedule.tasks)):
            c1, c2, c3, c4, c5 = st.columns([3, 2, 1, 2, 1])
            c1.write(t.task_name)
            c2.write(t.duration)
            c3.write(t.priority)
            c4.write(id_to_name.get(t.pet_id, "?"))
            if c5.button("✕", key=f"del_task_{i}"):
                owner.user_schedule.tasks.pop(i)
                st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Generate Schedule  →  generate_schedule() + update_pet_maintenance()
# ---------------------------------------------------------------------------
st.subheader("Generate Daily Schedule")

if st.button("Generate Schedule"):
    if not owner.user_schedule.tasks:
        st.warning("Add some tasks first.")
    else:
        owner.user_schedule.generate_schedule()     # <-- Schedule class method
        owner.update_pet_maintenance()              # <-- User class method (updates maintenance_level)

        placed = owner.user_schedule._placed
        if placed:
            st.success("Schedule generated successfully!")
            id_to_name = {p.id: p.name for p in owner.user_pets}

            # --- Sorted schedule table (tasks + blocked constraints merged by time) ---
            task_entries = [
                {
                    "Time": f"{Schedule._to_time(start)} – {Schedule._to_time(start + t.duration)}",
                    "Task": t.task_name,
                    "Duration (min)": t.duration,
                    "Priority": t.priority,
                    "Pet": id_to_name.get(t.pet_id, "?"),
                    "Status": t.status,
                    "_sort": start,
                }
                for start, t in owner.user_schedule.sort_by_time()
            ]
            constraint_entries = [
                {
                    "Time": f"{Schedule._to_time(b_start)} – {Schedule._to_time(b_end)}",
                    "Task": f"🚫 {owner.user_schedule.blocked_labels[i]}",
                    "Duration (min)": b_end - b_start,
                    "Priority": "—",
                    "Pet": "—",
                    "Status": "blocked",
                    "_sort": b_start,
                }
                for i, (b_start, b_end) in enumerate(owner.user_schedule.blocked_times)
            ]
            rows = sorted(task_entries + constraint_entries, key=lambda r: r["_sort"])
            for r in rows:
                del r["_sort"]
            st.table(rows)

            # --- Conflict warnings (uses detect_conflicts()) ---
            conflicts = owner.user_schedule.detect_conflicts()        # <-- Schedule.detect_conflicts()
            if conflicts:
                st.warning(f"⚠️ {len(conflicts)} scheduling conflict(s) detected:")
                for msg in conflicts:
                    st.warning(msg)
            else:
                st.success("No scheduling conflicts — all tasks fit cleanly!")

            # --- Unplaced tasks (uses check_conflicts()) ---
            unplaced = owner.user_schedule.check_conflicts()          # <-- Schedule.check_conflicts()
            if unplaced:
                st.warning(f"⚠️ {len(unplaced)} task(s) could not be placed (no free slot found):")
                for t in unplaced:
                    st.warning(f"  • {t.task_name} ({t.duration} min, priority {t.priority})")

            # --- Maintenance levels ---
            st.write("**Pet Maintenance Levels:**")
            maint_rows = [
                {"Pet": p.name, "Maintenance Score": f"{p.maintenance_level}/5"}
                for p in owner.user_pets
            ]
            st.table(maint_rows)


        else:
            st.warning("No tasks could be placed — all time slots may be blocked.")

st.divider()

# ---------------------------------------------------------------------------
# AI Schedule Generator
# Pipeline: retrieve_context → generate_ai_schedule → evaluate_schedule
#           → refinement loop → apply_ai_schedule → render in existing timeline
# ---------------------------------------------------------------------------
st.subheader("AI Schedule Generator")
st.caption(
    "Let AI plan the day for your pets based on their species, age, "
    "your constraints, and built-in care guidelines."
)

if not os.getenv("GROQ_API_KEY"):
    st.warning(
        "Set the `GROQ_API_KEY` environment variable to enable the AI pipeline.  \n"
        "Get a free key at **console.groq.com** → API Keys, then run:  \n"
        "`export GROQ_API_KEY=your-key` and restart the app."
    )
elif not owner.user_pets:
    st.info("Add at least one pet above before using the AI generator.")
else:
    # ── Human Review feedback box (persists between runs) ─────────────────
    human_feedback = st.text_input(
        "Optional — request specific changes "
        "(e.g. 'more exercise for Buddy, move dinner to 7pm')",
        value="",
        key="human_feedback_input",
        placeholder="Leave blank to let the AI decide",
    )

    col_run, col_status = st.columns([1, 3])
    with col_run:
        run_ai = st.button("Generate with AI", type="primary")

    # ── Run the pipeline ──────────────────────────────────────────────────
    if run_ai:
        with col_status:
            st.caption("Running: retrieve → plan → evaluate → refine…")

        try:
            with st.spinner("AI pipeline running…"):
                task_list, log_entries, reasoning = ai_pipeline.run_pipeline(
                    owner,
                    max_iterations=3,
                    human_feedback=human_feedback,
                )
                place_warnings = ai_pipeline.apply_ai_schedule(owner, task_list)
                owner.update_pet_maintenance()

            # Save results into session state so they survive the rerun
            st.session_state.ai_task_list = task_list
            st.session_state.ai_log_buffer = log_entries
            st.session_state.ai_reasoning = reasoning
            st.session_state.ai_warnings = place_warnings
            st.session_state.ai_schedule_applied = True
            st.rerun()

        except Exception as exc:
            msg = str(exc)
            if "api_key" in msg.lower() or "api key" in msg.lower() or "authentication" in msg.lower():
                st.error(
                    "Invalid API key. Check that `ANTHROPIC_API_KEY` is set correctly "
                    "and restart the app."
                )
            else:
                st.error(f"Pipeline error: {msg}")

    # ── Display the AI-generated schedule ────────────────────────────────
    if st.session_state.ai_schedule_applied and owner.user_schedule._placed:
        st.success("AI schedule generated and applied to your timeline!")

        id_to_name = {p.id: p.name for p in owner.user_pets}

        # Build the same merged table format as the manual generator
        task_entries = [
            {
                "Time": f"{Schedule._to_time(start)} – {Schedule._to_time(start + t.duration)}",
                "Task": t.task_name,
                "Duration (min)": t.duration,
                "Priority": t.priority,
                "Pet": id_to_name.get(t.pet_id, "?"),
                "Status": t.status,
                "_sort": start,
            }
            for start, t in owner.user_schedule.sort_by_time()
        ]
        constraint_entries = [
            {
                "Time": f"{Schedule._to_time(b_start)} – {Schedule._to_time(b_end)}",
                "Task": f"🚫 {owner.user_schedule.blocked_labels[i]}",
                "Duration (min)": b_end - b_start,
                "Priority": "—",
                "Pet": "—",
                "Status": "blocked",
                "_sort": b_start,
            }
            for i, (b_start, b_end) in enumerate(owner.user_schedule.blocked_times)
        ]
        rows = sorted(task_entries + constraint_entries, key=lambda r: r["_sort"])
        for r in rows:
            del r["_sort"]
        st.table(rows)

        # AI reasoning
        if st.session_state.ai_reasoning:
            with st.expander("AI Reasoning", expanded=False):
                st.write(st.session_state.ai_reasoning)

        # Placement warnings (unknown pet names, bad time strings, etc.)
        for w in st.session_state.ai_warnings:
            st.warning(w)

        # Maintenance levels
        st.write("**Pet Maintenance Levels (AI Schedule):**")
        st.table([
            {"Pet": p.name, "Maintenance Score": f"{p.maintenance_level}/5"}
            for p in owner.user_pets
        ])

        # ── Pipeline Log ─────────────────────────────────────────────────
        with st.expander("Pipeline Log", expanded=False):
            st.caption(
                "Each row is one step the AI pipeline logged. "
                "Expand to audit retrieve → plan → evaluate → apply."
            )
            for entry in st.session_state.ai_log_buffer:
                st.markdown(
                    f"`{entry['timestamp']}` &nbsp; **{entry['step']}**"
                )
                data = entry["data"]
                if isinstance(data, str):
                    # Truncate long prompts so the log stays readable
                    st.text(data[:400] + ("…" if len(data) > 400 else ""))
                else:
                    st.json(data)

    elif st.session_state.ai_schedule_applied:
        st.warning(
            "The AI pipeline ran but no tasks were placed. "
            "Check that your pets are set up and the schedule window is open."
        )

    st.divider()

    # ── Human Review — request another pass ───────────────────────────────
    st.subheader("Request Changes")
    st.caption(
        "Not happy with the AI schedule? Describe what to change above "
        "and click 'Generate with AI' again."
    )

    # ── Reliability Tests ─────────────────────────────────────────────────
    st.divider()
    st.subheader("Reliability Tests")
    st.caption("Runs predefined test cases against pipeline logic — no API calls made.")

    if st.button("Run Tests"):
        test_results = ai_pipeline.run_reliability_tests()
        passed = sum(1 for r in test_results if r["passed"])
        failed = [r for r in test_results if not r["passed"]]

        st.write(f"**{passed} / {len(test_results)} tests passed**")
        for r in test_results:
            icon = "✅" if r["passed"] else "❌"
            detail = r["detail"][:120] + ("…" if len(r["detail"]) > 120 else "")
            st.write(f"{icon} `{r['test']}` — {detail}")

        if failed:
            st.error(f"{len(failed)} test(s) failed — see details above.")
