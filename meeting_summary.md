# Freya Voice AI Hackathon — Kickoff Summary

## 1. Main objective: Build the Builder

The hackathon is about creating an AI agent that autonomously builds, tests, and improves other AI agents on Freya's platform.

There are two distinct systems:

- Builder Agent: The system you develop. It reads customer requirements, generates workflows, tests them, and iteratively improves them.
- Phone Agent: The final conversational agent your Builder creates on Freya's platform. This is what gets evaluated.

The initial use case is bank debt collection, but the Builder should generalize to other banking and insurance scenarios.

## 2. What your Builder needs to do

Unstructured customer requirements

PDFs, meeting notes, transcripts, instructions

Your Builder Agent

Extract rules → Generate workflow JSON → Deploy

Freya Phone Agent

Multi-agent conversational workflow

Simulate & evaluate

Personas, API calls, compliance, edge cases

Analyze failures and iterate until ready

Your Builder should be capable of completing this process with minimal human intervention.

## 3. What matters most for evaluation

The organizers emphasized that correctness and compliance are more important than simply collecting payments.

| Criterion             | What it means                                                             |
| --------------------- | ------------------------------------------------------------------------- |
| Rule compliance       | Follow all customer-specific instructions exactly                         |
| Identity verification | Never disclose sensitive information before authentication                |
| API correctness       | Call banking APIs with correct parameters                                 |
| Workflow routing      | Move between conversation stages only when permitted                      |
| Robustness            | Handle angry customers, scammers, prompt injection, and unusual scenarios |
| Generalization        | Build new workflows from previously unseen requirements                   |
| Speed                 | Generate and improve an agent within the three-hour window                |

For example, if a bank instructs an agent to ask for payment at most twice, asking a third time is a failure—even if it increases the likelihood of collection.

## 4. Hackathon timeline

Times described in the kickoff; organizers said they may change.

Saturday

Oct 10

Preparation and development

Explore Freya, understand its JSON workflows, build the initial Builder, and create local simulations.

Sunday

5:00 PM

New unseen requirements released

Receive additional complex banking/insurance scenarios.

5–8 PM

3 hours

Final building window

Your Builder must generate and optimize agents for the new scenarios. Official platform tests will be limited.

8–11 PM

Evaluation

Organizer evaluation

Public and private benchmark cases are evaluated, with results and rankings shared.

## 5. Technical details and constraints

- Platform: Final Phone Agents must run on Freya. The Builder can run anywhere.
- Workflow representation: Freya agents are represented as JSON workflows. The example shown contained approximately 638 lines.
- Integration: Freya will provide APIs and MCP connections to manipulate workflows programmatically.
- Tools: Codex, Claude Code, agent SDKs, and other LLM tools are allowed.
- Testing: Chat mode is recommended for fast iteration; voice is available for final testing.
- Mock banking APIs: Provided for identity verification and banking operations.
- Evaluation: Freya will simulate customer personas and test the final Phone Agent.
- Testing limits: Official platform evaluations will have limits, but local simulations can be run without organizer-imposed limits.
- Deliverables: A single workflow JSON was described as the primary final submission. Organizers also requested reproducible Builder skills and execution traces; exact submission instructions were still pending.
- Credits: The initial $10 testing budget will be increased, according to the organizers.

## 6. Recommended preparation strategy

Based on the kickoff, I would prioritize the following:

Preparation checklist

0/7 completed

Explore the provided Freya workflow manually and understand its JSON schema.

Identify how nodes, transitions, prompts, variables, and API tools are represented.

Build a requirements extractor that turns unstructured briefs into explicit rules and constraints.

Create a reusable workflow generator rather than hardcoding the debt-collection example.

Build a local test harness with synthetic customer personas and adversarial conversations.

Implement an evaluation loop that identifies violations, patches the workflow, and reruns tests.

Save reusable skills, generation traces, and the final workflow JSON.

## 7. Most important strategic insight

The organizers explicitly recommended learning the platform manually first, then converting that knowledge into reusable automation.

The strongest approach is likely to be a constraint-aware Builder with automated adversarial testing, rather than an agent that simply converts instructions into a large prompt.

For example, identity verification should be enforced through workflow transitions and API results, not just an LLM instruction saying never disclose debt information before verification.

That distinction matters because Freya is evaluating whether an agent can be induced to violate its rules, including through prompt injection and unusual customer behavior.

Bottom line: You're not primarily competing to create the best debt-collection chatbot. You're competing to build the most reliable, reusable system for turning messy enterprise requirements into compliant, tested Freya agents—quickly and reproducibly.