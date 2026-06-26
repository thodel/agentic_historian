---
name: "coding-qwen3"
description: "Route coding tasks to qwen3-coder-30b on GPUStack for fast, capable code generation and fixing."
---

# Skill: coding-qwen3

Use qwen3-coder-30b running on the local GPUStack cluster for coding tasks.

## Setup

Credentials are stored in `.env.gpustack` in the workspace:
- `GPUSTACK_API_KEY`
- `GPUSTACK_BASE_URL=https://gpustack.unibe.ch/v1`
- `GPUSTACK_MODEL=qwen3-coder-30b-a3b-instruct`

## When to Use

Route a coding task here when:
- The user asks to write, fix, refactor, or explain code
- A task is primarily about code (as opposed to general reasoning or conversation)
- You want faster/cheaper inference than the default model for pure coding work

## How to Call It

Use `sessions_spawn` with `runtime="subagent"` and a focused coding prompt:

```python
sessions_spawn(
    task="""[FOCUS: coding task description]
    
    Requirements:
    - [specific requirements]
    - [expected input/output if applicable]
    
    If files need to be edited, use the write/edit tools to apply changes.
    When done, briefly summarize what was changed.""",
    taskName="coding",
    runtime="subagent",
    model="qwen3-coder-30b-a3b-instruct",
    attachments=[...],  # pass relevant files if needed
)
```

## Invocation

User-facing trigger: `/coding <task>` or just detecting a coding intent.

Internal trigger: when a coding task is identified, spawn a subagent with `model="qwen3-coder-30b-a3b-instruct"` and `runtime="subagent"`.

## Notes

- This model is a code specialist — best for pure coding, not general conversation.
- Use `lightContext=true` for simple tasks to reduce token usage.
- Large file edits should be done by passing the file content in attachments or via the workspace `read` tool.
