# TF Notes: Show What You Know — Applied AI System

*A review of the assignment itself — what it's actually asking for, where students commonly misread it, and an FAQ/guide for whoever does this next. I completed this using a pet-care scheduler (PawPal+) as my chosen project, and I pull a few concrete details from that build in where they illustrate a point — but the guidance below is meant to hold regardless of which prior project someone extends.*

---

## 1. What This Assignment Is Actually Asking For

Strip away the specifics and the assignment has one real thesis: **take something you already built, and make it trustworthy, not just impressive.** Every section of the instructions is in service of that:

- **Functionality** asks you to add one real AI capability (RAG, agentic workflow, fine-tuned model, or a reliability/testing system) — but the load-bearing sentence is easy to skim past: *"The feature should be fully integrated into the main application logic... not enough to have a standalone script."* That line is the actual bar for this whole section, and it's the one most likely to quietly fail a submission that otherwise looks complete.
- **Design and architecture** wants a diagram that shows data flow *and* where a human or a test sits in that flow — not just a pretty box diagram of your classes.
- **Documentation** is really asking you to write for someone who has never seen your code and never will run it live — hence the insistence on pasted sample interactions instead of "trust me, it works."
- **Reliability and evaluation** wants evidence, not a claim. "It works" is not a testing summary; "5 of 6 tests passed, it struggled when X" is.
- **Reflection and ethics** is graded separately from your README reflection and is specifically about your judgment as a collaborator with AI — not about the project's features.

If you only remember one thing from this document: **every one of these requirements is checking whether you can tell the difference between something that looks done and something that is actually verified.** That's the whole assignment.

---

## 2. The Four AI Feature Options, Defined

The instructions name four qualifying features and give one line of example each. Here's what they actually mean in practice, what a real implementation looks like, and what the shallow/failing version of each tends to look like — since "standalone script, not integrated" shows up in a different disguise for each one.

### Retrieval-Augmented Generation (RAG)
**What it means:** before the model generates anything, your code looks up relevant information from some source — a document store, a local knowledge base, a database, search results — and puts that information into the prompt. The model's answer is grounded in what was retrieved, not just what it already "knows."

**What it looks like done well:** the retrieved content measurably changes the output. Different input → different retrieval → different generation. The retrieval step should be doing real filtering or lookup work (matching on entity, category, keyword, embedding similarity — something), not just concatenating your entire knowledge base into every prompt regardless of relevance.

**What the shallow version looks like:** a function that fetches or prints some context, followed by an LLM call that either ignores it or would have produced the same answer anyway. If you can't point to a specific input where the retrieved content changed the model's behavior, it isn't really RAG yet — it's a lookup table with an LLM call bolted on next to it.

### Agentic Workflow
**What it means:** the AI doesn't just answer once — it plans a sequence of steps, takes actions (which might include calling tools, writing code, or invoking itself again), and checks its own work, potentially looping or correcting itself before returning a final result to the user.

**What it looks like done well:** there's a real loop — plan → act → evaluate/check → (revise and repeat, or stop) — and the "check" step can actually change the outcome (trigger a retry, a correction, or a rejection), not just log that a check happened.

**What the shallow version looks like:** a single LLM call that outputs multiple steps of reasoning in one shot ("chain of thought") but never actually executes or verifies any of them. If there's no second pass where output from step 1 is inspected and can alter what happens in step 2, it's not yet an agentic loop — it's just a more verbose single call.

### Fine-Tuned or Specialized Model
**What it means:** you use a model that has been adapted — through actual fine-tuning, or through a rigorous specialization layer (a carefully constructed system prompt plus few-shot examples plus output constraints, applied consistently) — to reliably perform one narrow task or match one voice/format, rather than using a general-purpose model as-is.

**What it looks like done well:** you can show the specialized behavior is consistent and distinct from what the base model would do unprompted — a particular tone, a particular output schema, a particular domain vocabulary, enforced across many inputs, not just achieved once by luck.

**What the shallow version looks like:** a one-line system prompt ("you are a helpful assistant for X") with no evidence that it actually changes behavior reliably, and no comparison against the unspecialized baseline. If you're going this route without actual fine-tuning infrastructure, you need to show the specialization holds up across varied inputs, not just in your one demo run.

### Reliability or Testing System
**What it means:** your project includes a structured way to measure how well the AI performs — automated tests with pass/fail assertions, confidence scoring the AI produces about its own output, systematic logging and error handling that tracks failures, or documented human evaluation against stated criteria.

**What it looks like done well:** the mechanism produces evidence you can point to — a test suite that actually runs and reports pass/fail counts, a confidence score that's computed from something meaningful (not a constant), or a human-evaluation table with real criteria and real results, including failures.

**What the shallow version looks like:** a handful of manual "I tried it and it worked" runs with no structure, or a testing script that exists but was never actually run as part of the submitted evidence. This is also the feature option easiest to combine with any of the other three as a second layer — worth considering even if you pick RAG or agentic as your primary required feature.

**Choosing between them:** don't pick based on which sounds most advanced — pick based on which one your existing project already has the right shape for. A project with no real data source to retrieve from will produce a weak RAG feature; a project with one clear multi-step task (generate → verify → fix) will produce a strong agentic feature almost for free. See the trust-boundary question in Step 2 of the guide below — it applies to all four options.

---

## 3. Section-by-Section Walkthrough

### Functionality
Pick the one required feature that your *existing* project already has the right shape for, rather than the one that sounds most impressive. If your Module 1-3 project has no real knowledge source, forcing in RAG usually produces the "standalone script" failure mode — a retriever function that runs and prints something next to an unrelated LLM call. An agentic plan → act → check loop, or a reliability/testing harness, is often the more honest fit if there's nothing natural to retrieve.

The self-check that matters here: **if you deleted your AI feature, would the app's behavior visibly change beyond "the AI part is now missing"?** If retrieval context were empty, would the model's output actually differ? If an agent's self-check step were skipped, would anything downstream behave differently? If the answer is no, the feature isn't integrated yet, it's decoration.

### Design and Architecture
Two diagrams are implicitly expected, even though the instructions only say "a short system diagram": one for your existing data model (if you have one worth showing) and one for the actual AI pipeline — components, data flow, and *specifically* where a human or an automated test intervenes. That last part is the one people forget to draw. A human reviewing/approving AI output, a user correcting it in natural language, or a test suite gating what reaches the UI — any of these count, but the diagram has to show it, not just your code.

Practical note: the submission checklist wants the Mermaid source in a `diagrams/` folder, not floating at repo root, and wants it as a real `.mmd`/`.md` file, not a screenshot of a diagram tool.

### Documentation
The README is written for "a future employer who might look at your GitHub portfolio," per the instructions — that's a specific, useful constraint. It means: state your original project by name up front (2-3 sentences on what it originally did), then treat everything after that as if the reader has zero context on your coursework. Sample interactions should be pasted as fenced code blocks, not screenshots, and the instructions want 2-3 of them — pick ones that each demonstrate something different rather than three near-identical happy-path runs.

Design decisions should name the trade-off explicitly, not just describe the choice. "I chose X" is a decision; "I chose X, which means Y is worse, but that was worth it because Z" is a design decision as the rubric wants it.

### Reliability and Evaluation
Pick one mechanism, but pick the one that's honest for what you built: automated tests, confidence scoring, structured logging/error handling, or human evaluation. The instructions explicitly want the results in a *parseable* format — a markdown table or structured text, not "I tried a bunch of inputs and it seemed fine." If you use human evaluation, the table format the instructions show (Test Input / Evaluation Criteria / Result) is worth copying directly — it's easy to grade and easy to write.

The part people skip: report the failures, not just the passes. "5 out of 6 tests passed; the AI struggled when context was missing" is the literal example given in the instructions — a summary that hides your one failing case is weaker evidence than one that names it.

### Reflection and Ethics
This is graded from `model_card.md`, separately from the README's reflection section — and the instructions are explicit that reflection content placed *only* in the README does not earn these points. It has to answer, concretely: how you collaborated with AI (with one specific example of a good suggestion and one specific example of a bad one you caught), and what your system's limitations and biases actually are. Vague answers here ("AI could have biases") score worse than a specific one grounded in something you actually observed while building.

### Submission Checklist
Worth reading literally, top to bottom, right before you submit — it's less about content and more about file placement, and those are the easiest points to lose for reasons that have nothing to do with the quality of your work:
- Repo public, functional code, `README.md`, `model_card.md`, and a Mermaid diagram file all present.
- Diagram source (and any supplementary images) in `diagrams/` or `/assets`, not repo root.
- Reproducible outputs or interaction logs as markdown/`.txt`, not only demo screenshots.
- Commit history shows multiple meaningful commits — not one giant final commit.
- README has 2-3 real input/output examples in fenced code blocks.

---

## 4. Common Q&A

**Q: Do I have to use RAG specifically?**
A: No — any one of RAG, agentic workflow, fine-tuned/specialized model, or reliability/testing system satisfies the requirement. Choose based on what your existing project's data or logic naturally supports, not based on which sounds most advanced.

**Q: What does "fully integrated, not a standalone script" actually mean?**
A: The grader should not be able to remove your AI feature and have the app behave identically minus the AI. Test this on yourself before you submit: trace one real input through your code and confirm the AI feature's output actually changes what the user sees, not just that it runs and logs something alongside an otherwise-unrelated response.

**Q: How much should I actually trust the AI to get right?**
A: Less than expected, and figuring that out through testing is a real part of the assignment, not a distraction from it. LLMs are reliably good at understanding language and reliably inconsistent at following explicit numeric or structural rules stated in a prompt (spacing constraints, exact counts, ordering). If you find your AI ignoring an instruction some meaningful fraction of the time no matter how you reword the prompt, that's a legitimate finding to build around — move the rule into code you control, and say so in your design decisions section.

**Q: My evaluator/checker step approves almost everything — is that a bug I need to hide?**
A: No — it's a finding, and reporting it honestly is worth more than a testing summary that only shows passes. A checking mechanism that rubber-stamps bad output is itself a reliability result worth documenting.

**Q: Where does the "human in the loop" checkpoint need to show up?**
A: In your diagram, explicitly. It can be as simple as "user reviews generated output before accepting it" or a dedicated correction/feedback step — but the instructions ask you to show where humans or testing check the AI's results, and that has to be visible in the diagram itself, not just implied by your code.

**Q: Do I need a numeric confidence score?**
A: No — it's listed as one example mechanism among several, not a requirement. Pick whichever reliability mechanism is genuine for your system rather than adding a confidence number that isn't actually computed from anything meaningful.

**Q: What makes a strong sample interaction, versus a weak one?**
A: A weak one shows only the happy path. A strong one demonstrates an edge case or a specific feature within the same example — e.g., an input with a typo the system corrects, or one that triggers a fallback path — so your 2-3 examples collectively cover more than three near-identical runs would.

**Q: Where do the two different reflections go?**
A: README's reflection section is about what the *project* taught you — process, problem-solving, what you'd change. `model_card.md` is specifically the responsible-AI reflection: bias, misuse potential, testing surprises, and a concrete good/flawed AI-collaboration example. They are graded from different files; putting model-card content only in the README loses those points even if the writing itself is good.

**Q: I'm extending a totally different kind of project than a scheduler/chatbot — does any of this still apply?**
A: Yes — none of the above is specific to any one project type. The trust-boundary question ("what can the AI get wrong safely, and what must your own code guarantee?") applies whether you're extending a text summarizer, a game, a data pipeline, or a scheduler. Pick your required feature based on your project's actual shape, not based on matching someone else's example.

---

## 5. Guide for Future Students

**Step 0 — Pick the prior project with the clearest existing structure**, not necessarily the most interesting one. You're extending it, which is much lower-risk than rewriting it — a project with an existing data model or rule engine gives the AI layer something concrete to sit on top of.

**Step 1 — Choose your required AI feature based on what your project already has**, not based on the order the instructions list them in. Ask: does this project have a real knowledge source (→ RAG), a multi-step task the AI could plan/act/check (→ agentic), a need to measure its own reliability (→ testing system), or a case for a specialized model? Pick the one that's true, not the one that sounds best on a resume.

**Step 2 — Decide your trust boundary before writing any prompts.** Write down, in plain language: what is the AI allowed to get wrong, and what must your own code guarantee no matter what the AI outputs? This single decision tends to become your architecture diagram, your design-decisions section, and your reflection, almost for free.

**Step 3 — Build and test the deterministic parts first**, then wire in the AI call. Pure functions are far easier to unit test than AI output; get that safety net working before the model starts doing something unpredictable to it.

**Step 4 — Instrument logging from day one**, not the week of the deadline. A single logging function that every stage of your pipeline calls gives you both a debugging tool and free evidence for the "reproducible execution logs" requirement.

**Step 5 — Create your `diagrams/` folder and both `.mmd` files early**, before you've forgotten what the pipeline actually looks like. Retrofitting a diagram after the code is finished tends to produce a diagram of what you meant to build, not what you actually built.

**Step 6 — Test against your worst-case inputs, not your demo inputs.** The most useful reliability findings come from ambiguous, conflicting, or incomplete input — not from the three clean examples you plan to put in the README.

**Step 7 — Write `model_card.md` while the bugs are still fresh**, not after you've moved on. Reflections written immediately after a specific bug are noticeably more concrete than ones reconstructed from memory later.

**Step 8 — Before submitting, walk the submission checklist file by file**, not feature by feature. It's entirely possible to have every feature requirement satisfied in spirit while still losing points on file placement (diagram location, root-level clutter, log format) — those are avoidable losses that have nothing to do with how good the underlying work is.

---

## 6. One Concrete Example, for Illustration

For reference, in my own submission (PawPal+, an AI-assisted pet care scheduler extending an OOP scheduling project from Modules 1-3): I chose RAG plus a reliability/testing system as my two AI features, drew the trust boundary as "the AI assigns timing only, Python guarantees task completeness and spacing," and the most useful thing testing surfaced was that the LLM ignored an explicit spacing rule in the prompt on roughly half of all runs regardless of rewording — which is what pushed that constraint into code rather than prompt engineering. That single finding ended up anchoring the design-decisions section, the reliability summary, and the reflection all at once — which is the pattern worth reproducing, regardless of what project or feature someone else chooses.
