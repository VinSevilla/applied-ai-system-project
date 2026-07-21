import os

import streamlit as st

import ai_pipeline
from pawpal_system import Schedule, Pet, User

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
if "care_instructions" not in st.session_state:
    st.session_state.care_instructions = ""

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
# Care Instructions — free-text requests fed into the AI planner
# ---------------------------------------------------------------------------
st.subheader("Care Instructions")
st.caption("Describe what you need done for each pet. The AI will turn this into a schedule.")

care_instructions = st.text_area(
    "What should the AI plan for your pets?",
    value=st.session_state.care_instructions,
    placeholder=(
        "e.g. Feed Mochi twice a day, give Garfield his medicine at 8am, "
        "walk Buddy in the morning and evening, groom Mochi once today"
    ),
    height=100,
    key="care_instructions_input",
)

if care_instructions != st.session_state.care_instructions:
    st.session_state.care_instructions = care_instructions

# Show pre-parsed task intents so the user sees exactly what was interpreted
if st.session_state.care_instructions.strip() and owner.user_pets:
    intents = ai_pipeline._parse_care_instructions(
        st.session_state.care_instructions, owner.user_pets
    )
    if intents:
        st.success("Care instructions accepted — tasks interpreted:")
        for t in intents:
            combined_note = " *(shared slot)*" if t["combined"] else ""
            time_note = f" at {t['time_hint']}" if t["time_hint"] else ""
            st.markdown(f"&nbsp;&nbsp;• **{t['task_name']}** → {t['pet_label']}{time_note}{combined_note}")

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

def _run_pipeline(feedback: str = ""):
    """Run the AI pipeline and save results to session state."""
    try:
        with st.spinner("AI pipeline running…"):
            task_list, log_entries, reasoning = ai_pipeline.run_pipeline(
                owner,
                max_iterations=3,
                human_feedback=feedback,
                care_instructions=st.session_state.care_instructions,
            )
            place_warnings = ai_pipeline.apply_ai_schedule(owner, task_list)
            owner.update_pet_maintenance()

        st.session_state.ai_task_list = task_list
        st.session_state.ai_log_buffer = log_entries
        st.session_state.ai_reasoning = reasoning
        st.session_state.ai_warnings = place_warnings
        st.session_state.ai_schedule_applied = True
        st.rerun()

    except Exception as exc:
        msg = str(exc)
        if "api_key" in msg.lower() or "api key" in msg.lower() or "authentication" in msg.lower():
            st.error("Invalid API key. Check that `GROQ_API_KEY` is set correctly and restart the app.")
        else:
            st.error(f"Pipeline error: {msg}")

if not os.getenv("GROQ_API_KEY"):
    st.warning(
        "Set the `GROQ_API_KEY` environment variable to enable the AI pipeline.  \n"
        "Get a free key at **console.groq.com** → API Keys, then run:  \n"
        "`export GROQ_API_KEY=your-key` and restart the app."
    )
elif not owner.user_pets:
    st.info("Add at least one pet above before using the AI generator.")
elif not st.session_state.care_instructions.strip():
    st.info("Add care instructions above so the AI knows what to schedule for your pets.")
else:
    # ── Generate button ───────────────────────────────────────────────────
    if st.button("Generate with AI", type="primary"):
        _run_pipeline()

    # ── Display the AI-generated schedule ────────────────────────────────
    if st.session_state.ai_schedule_applied and owner.user_schedule._placed:
        st.success("AI schedule generated!")

        id_to_name = {p.id: p.name for p in owner.user_pets}

        task_entries = [
            {
                "Time": f"{Schedule._to_time(start)} – {Schedule._to_time(start + t.duration)}",
                "Task": t.task_name,
                "Duration (min)": t.duration,
                "Priority": t.priority,
                # Use _display_pets if set (merged multi-pet slot), else fall back to single pet name
                "Pet": getattr(t, "_display_pets", None) or id_to_name.get(t.pet_id, "?"),
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

        # Header
        h = st.columns([2, 2, 1, 1, 2, 1])
        for col, lbl in zip(h, ["Time", "Task", "Min", "Pri", "Pet", "Status"]):
            col.markdown(f"**{lbl}**")
        st.divider()

        for row in rows:
            start_min = row["_sort"]
            is_blocked = row["Status"] == "blocked"
            task_obj = owner.user_schedule._placed.get(start_min)

            c = st.columns([2, 2, 1, 1, 2, 1])
            c[0].write(row["Time"])
            c[1].write(row["Task"])
            c[2].write(row["Duration (min)"])
            c[3].write(row["Priority"])
            c[4].write(row["Pet"])

            if is_blocked:
                c[5].write("🚫 blocked")
            elif task_obj:
                is_done = task_obj.status == "complete"
                if is_done:
                    if c[5].button("✅ Done", key=f"undo_{start_min}"):
                        task_obj.status = "pending"
                        st.rerun()
                else:
                    if c[5].button("Mark done", key=f"done_{start_min}"):
                        task_obj.mark_complete()
                        st.rerun()
            else:
                c[5].write(row["Status"])

        if st.session_state.ai_reasoning:
            with st.expander("AI Reasoning", expanded=False):
                st.write(st.session_state.ai_reasoning)

        for w in st.session_state.ai_warnings:
            st.warning(w)

        st.write("**Pet Maintenance Levels:**")
        st.table([
            {"Pet": p.name, "Maintenance Score": f"{p.maintenance_level}/5"}
            for p in owner.user_pets
        ])

        with st.expander("Pipeline Log", expanded=False):
            for entry in st.session_state.ai_log_buffer:
                st.markdown(f"`{entry['timestamp']}` &nbsp; **{entry['step']}**")
                data = entry["data"]
                if isinstance(data, str):
                    st.text(data[:400] + ("…" if len(data) > 400 else ""))
                else:
                    st.json(data)

        # ── Human Review ─────────────────────────────────────────────────
        st.divider()
        st.subheader("Request Changes")
        st.caption("Describe what you'd like to change and the AI will regenerate the schedule.")

        with st.form("request_changes_form"):
            change_request = st.text_area(
                "What would you like to change?",
                placeholder="e.g. more exercise for Buddy, move dinner to 7pm, add a grooming session",
                height=80,
            )
            apply_changes = st.form_submit_button("Apply Changes", type="primary")

        if apply_changes:
            if change_request.strip():
                _run_pipeline(feedback=change_request.strip())
            else:
                st.warning("Please describe the changes you'd like before submitting.")

    elif st.session_state.ai_schedule_applied:
        task_count = len(st.session_state.ai_task_list or [])
        if task_count == 0:
            st.warning(
                "The AI returned no tasks. Make sure your care instructions describe "
                "specific tasks (e.g. 'Feed Mochi twice a day, walk Buddy in the morning')."
            )
        else:
            st.warning(
                f"The AI generated {task_count} task(s) but none could be placed. "
                "Check the details below."
            )
        for w in (st.session_state.ai_warnings or []):
            st.error(w)
        with st.expander("Pipeline Log", expanded=True):
            for entry in (st.session_state.ai_log_buffer or []):
                st.markdown(f"`{entry['timestamp']}` &nbsp; **{entry['step']}**")
                data = entry["data"]
                if isinstance(data, str):
                    st.text(data[:600] + ("…" if len(data) > 600 else ""))
                else:
                    st.json(data)

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
