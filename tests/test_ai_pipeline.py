"""
tests/test_ai_pipeline.py — Reliability tests for the PawPal+ AI pipeline.

All tests are logic-only — no Anthropic API calls are made.
Run with:  pytest tests/test_ai_pipeline.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pawpal_system import Pet, Task, Schedule, User
from ai_pipeline import (
    KNOWLEDGE_BASE,
    apply_ai_schedule,
    clear_log_buffer,
    get_log_buffer,
    log_pipeline_step,
    retrieve_context,
    run_reliability_tests,
)


# ---------------------------------------------------------------------------
# Retriever tests
# ---------------------------------------------------------------------------

def test_retrieve_context_includes_pet_name():
    """retrieve_context should mention the pet by name."""
    pets = [Pet(id=1, name="Buddy", type="Dog", age=3)]
    ctx = retrieve_context(pets)
    assert "Buddy" in ctx


def test_retrieve_context_dog_includes_walk():
    """Dog guidelines should mention walking."""
    pets = [Pet(id=1, name="Buddy", type="Dog", age=3)]
    ctx = retrieve_context(pets)
    assert "walk" in ctx.lower()


def test_retrieve_context_cat_includes_litter():
    """Cat guidelines should mention litter."""
    pets = [Pet(id=1, name="Mochi", type="Cat", age=2)]
    ctx = retrieve_context(pets)
    assert "litter" in ctx.lower()


def test_retrieve_context_puppy_note_for_young_dog():
    """Dogs under age 2 should trigger the puppy age note."""
    pets = [Pet(id=1, name="Pup", type="Dog", age=1)]
    ctx = retrieve_context(pets)
    assert "pupp" in ctx.lower()   # matches "Puppies" or "puppy"


def test_retrieve_context_no_puppy_note_for_adult_dog():
    """Adult dogs (age >= 2) should NOT receive the puppy note."""
    pets = [Pet(id=1, name="Rex", type="Dog", age=4)]
    ctx = retrieve_context(pets)
    assert "puppy" not in ctx.lower()


def test_retrieve_context_senior_note_for_old_dog():
    """Dogs age 8+ should trigger the senior age note."""
    pets = [Pet(id=1, name="Gramps", type="Dog", age=10)]
    ctx = retrieve_context(pets)
    assert "senior" in ctx.lower()


def test_retrieve_context_unknown_type_falls_back():
    """Unknown pet types should fall back to the 'Other' knowledge base entry."""
    pets = [Pet(id=1, name="Tweety", type="Bird", age=1)]
    ctx = retrieve_context(pets)
    assert "Tweety" in ctx
    assert len(ctx) > 50   # not empty


def test_retrieve_context_multiple_pets():
    """retrieve_context should include guidelines for all pets."""
    pets = [
        Pet(id=1, name="Buddy", type="Dog", age=3),
        Pet(id=2, name="Whiskers", type="Cat", age=5),
    ]
    ctx = retrieve_context(pets)
    assert "Buddy" in ctx
    assert "Whiskers" in ctx


def test_knowledge_base_has_required_types():
    """KNOWLEDGE_BASE must cover Dog, Cat, and Other."""
    for key in ("Dog", "Cat", "Other"):
        assert key in KNOWLEDGE_BASE, f"KNOWLEDGE_BASE missing key: {key}"
        assert "recommended_tasks" in KNOWLEDGE_BASE[key]
        assert "general" in KNOWLEDGE_BASE[key]


# ---------------------------------------------------------------------------
# apply_ai_schedule tests
# ---------------------------------------------------------------------------

def test_apply_ai_schedule_places_valid_tasks():
    """Valid task dicts should be placed into Schedule._placed."""
    owner = User(id=1, name="Alex")
    owner.add_pet(Pet(id=1, name="Buddy", type="Dog", age=3))

    tasks = [
        {"task_name": "Morning Walk", "start_time": "7:00am",
         "duration": 30, "priority": 5, "pet_name": "Buddy"},
        {"task_name": "Dinner",       "start_time": "6:00pm",
         "duration": 15, "priority": 4, "pet_name": "Buddy"},
    ]
    warnings = apply_ai_schedule(owner, tasks)

    assert len(owner.user_schedule._placed) == 2
    assert warnings == []


def test_apply_ai_schedule_sort_by_time_works_after():
    """After apply_ai_schedule, sort_by_time() should return tasks in order."""
    owner = User(id=1, name="Alex")
    owner.add_pet(Pet(id=1, name="Buddy", type="Dog", age=3))

    tasks = [
        {"task_name": "Dinner",       "start_time": "6:00pm",
         "duration": 15, "priority": 4, "pet_name": "Buddy"},
        {"task_name": "Morning Walk", "start_time": "7:00am",
         "duration": 30, "priority": 5, "pet_name": "Buddy"},
    ]
    apply_ai_schedule(owner, tasks)

    ordered = owner.user_schedule.sort_by_time()
    starts = [s for s, _ in ordered]
    assert starts == sorted(starts)
    assert ordered[0][1].task_name == "Morning Walk"


def test_apply_ai_schedule_skips_unknown_pet():
    """Tasks for a pet name not in owner.user_pets should be skipped with a warning."""
    owner = User(id=2, name="Sam")
    owner.add_pet(Pet(id=1, name="Luna", type="Cat", age=2))

    tasks = [
        {"task_name": "Walk", "start_time": "9:00am",
         "duration": 30, "priority": 3, "pet_name": "GhostPet"},
    ]
    warnings = apply_ai_schedule(owner, tasks)

    assert len(owner.user_schedule._placed) == 0
    assert len(warnings) == 1
    assert "GhostPet" in warnings[0]


def test_apply_ai_schedule_clears_previous_schedule():
    """apply_ai_schedule should wipe the existing schedule before placing new tasks."""
    owner = User(id=3, name="Pat")
    owner.add_pet(Pet(id=1, name="Rex", type="Dog", age=4))

    # Pre-populate the schedule
    old = Task(task_name="Old Task", duration=20, priority=3, pet_id=1)
    owner.user_schedule.add_task_at(old, "8:00am")
    assert len(owner.user_schedule._placed) == 1

    # Apply an empty AI list — should clear everything
    apply_ai_schedule(owner, [])
    assert len(owner.user_schedule._placed) == 0


def test_apply_ai_schedule_pet_name_case_insensitive():
    """Pet name matching should be case-insensitive."""
    owner = User(id=4, name="Jordan")
    owner.add_pet(Pet(id=1, name="Buddy", type="Dog", age=3))

    tasks = [
        {"task_name": "Walk", "start_time": "8:00am",
         "duration": 30, "priority": 5, "pet_name": "buddy"},   # lowercase
    ]
    warnings = apply_ai_schedule(owner, tasks)

    assert len(owner.user_schedule._placed) == 1
    assert warnings == []


# ---------------------------------------------------------------------------
# Logger tests
# ---------------------------------------------------------------------------

def test_log_pipeline_step_appends_to_buffer():
    """log_pipeline_step should add an entry to the in-memory buffer."""
    clear_log_buffer()
    log_pipeline_step("test_step", {"key": "value"})

    buf = get_log_buffer()
    assert len(buf) == 1
    assert buf[0]["step"] == "test_step"
    assert buf[0]["data"] == {"key": "value"}
    assert "timestamp" in buf[0]


def test_log_pipeline_step_multiple_entries():
    """Multiple log_pipeline_step calls should accumulate in order."""
    clear_log_buffer()
    log_pipeline_step("step_a", "data_a")
    log_pipeline_step("step_b", "data_b")

    buf = get_log_buffer()
    assert len(buf) == 2
    assert buf[0]["step"] == "step_a"
    assert buf[1]["step"] == "step_b"


def test_clear_log_buffer_empties_buffer():
    """clear_log_buffer should leave the buffer empty."""
    log_pipeline_step("some_step", "some_data")
    clear_log_buffer()
    assert get_log_buffer() == []


def test_get_log_buffer_returns_copy():
    """get_log_buffer should return a copy, not a reference to the internal list."""
    clear_log_buffer()
    log_pipeline_step("x", 1)

    buf = get_log_buffer()
    buf.clear()  # mutate the returned copy

    # Internal buffer should still have the entry
    assert len(get_log_buffer()) == 1


# ---------------------------------------------------------------------------
# run_reliability_tests (self-check)
# ---------------------------------------------------------------------------

def test_run_reliability_tests_all_pass():
    """run_reliability_tests() should report all internal tests as passing."""
    results = run_reliability_tests()
    assert len(results) > 0, "run_reliability_tests returned no results"

    failed = [r for r in results if not r["passed"]]
    assert failed == [], f"Failing reliability tests: {[r['test'] for r in failed]}"
