---

description: Documentation standards optimized for both human and LLM context quality and long-term maintainability
globs:

- "**/*"
alwaysApply: true

---

# Documentation Management

- Treat documentation as structured project context, not prose.
- Optimize for both human skim readers and LLM context ingestion.
- Remove outdated or misleading information immediately.

# When to Create or Update README.md

- Create README when starting a project.
- Update README in the same commit as major changes (features, APIs, architecture, etc)
- Do NOT update for minor bug fixes, refactors without behavior change, comments, or patch-level dependency bumps.

# Writing Style

- Limit each section to 1–3 sentences.
- Be direct, factual, and concrete; avoid marketing language.
- Prefer executable examples and commands over abstract descriptions.
- Use informative headings that fully describe the section’s content.
- Remove all template placeholders (e.g., `{{VAR}}`).
- No need to document the API, if one exists

# Required README Structure

See file in root of project: `README.md.template`

## README template
Copy of `README.md.template` so you don't have to open another file:
```markdown
# {{PROJECT_NAME}}

> **Purpose**: {{ONE_SENTENCE_PROBLEM_STATEMENT}}
> **Status**: {{ACTIVE|EXPERIMENTAL|ARCHIVED}} | Last Updated: {{DATE}}

## What This Solves

{{2-3_SENTENCES_EXPLAINING_THE_SPECIFIC_PROBLEM_THIS_ADDRESSES}}

## Configuration

This project uses a dual configuration system for security. See `docs/CONFIGURATION.md` for step-by-step guidance on adding new flags or secrets.

### 1. Non-Secret Config (pyproject.toml)
Version-controlled settings in `[tool.config]`:
```toml
[tool.config]
flask_port = {{PORT}}
database_path = "data/{{DATABASE_FILE}}"
# ... other non-secret settings
```

### 2. Secrets (src/values.py - Git-Ignored)
Sensitive data like API keys:
```python
# src/values.py.example (create this file, then copy it to src/values.py)
TELEGRAM_API_TOKEN = "your_token"
YOUR_API_KEY = "your_key"
```

### View Config
```bash
uv run config --all        # Show all non-secret config
uv run config --flask-port # Get specific value
uv run config --help       # See all options
```

## Quick Start
```bash
# Install dependencies
uv sync

# Set up secrets (if needed)
cp template-project/src/values.py.example src/values.py
# Edit src/values.py with your actual secrets

# Run
uv run python app.py
```

Server runs at http://localhost:{{PORT}}

## Architecture

### Mental Model
{{EXPLAIN_THE_CORE_ABSTRACTION_OR_PATTERN}}

For example: "This is a webhook receiver that accepts events from Home Assistant, transforms them, and stores them for later analysis."
```mermaid
flowchart LR
    subgraph External
        {{EXTERNAL_SERVICE}}[{{EXTERNAL_NAME}}]
    end
    subgraph Storage
        DB[({{DATABASE_FILE}})]
    end
    subgraph App
        Server[{{FRAMEWORK}} Server :{{PORT}}]
    end
    
    {{EXTERNAL_SERVICE}} -->|{{API_ACTION}}| Server
    Server --> DB
```

### Data Flow
1. {{STEP_1_WHAT_HAPPENS}}
2. {{STEP_2_WHAT_HAPPENS}}
3. {{STEP_3_WHAT_HAPPENS}}

**Key Decision**: {{WHY_THIS_ARCHITECTURE_OVER_ALTERNATIVES}}

## Tech Stack & Why

| Technology | Purpose | Why This Choice |
|------------|---------|-----------------|
| {{LANGUAGE_VERSION}} | Runtime | {{REASON_FOR_VERSION}} |
| {{FRAMEWORK}} | Web framework | {{WHY_NOT_ALTERNATIVES}} |
| {{DATABASE}} | Storage | {{WHY_THIS_DB}} |
| {{OTHER_TECH}} | {{PURPOSE}} | {{RATIONALE}} |

## Project Structure

```
{{REPO_NAME}}/
├── app.py                    # Main entry point: routes & server setup
├── datamodels.py             # Pydantic/dataclass models for type safety
├── db.py                     # Database connection & query utilities
├── pyproject.toml            # Dependencies & tool config
│
└── install/                  # Deployment scripts (optional)
    └── install.sh
```

**Organization Logic**: {{EXPLAIN_WHY_FILES_ARE_SPLIT_THIS_WAY}}

## Key Concepts

| Concept | Description | Why It Matters |
|---------|-------------|----------------|
| **{{CONCEPT_1}}** | {{CONCEPT_1_DESCRIPTION}} | {{WHY_UNDERSTANDING_THIS_HELPS}} |
| **{{CONCEPT_2}}** | {{CONCEPT_2_DESCRIPTION}} | {{IMPACT_ON_DEVELOPMENT}} |

## Data Models
```python
{{MODEL_NAME}}
├── {{FIELD_1}}: {{FIELD_1_TYPE}}  # {{PURPOSE_OR_CONSTRAINT}}
├── {{FIELD_2}}: {{FIELD_2_TYPE}}  # {{VALIDATION_RULES_IF_ANY}}
└── {{FIELD_3}}: {{FIELD_3_TYPE}}  # {{OPTIONAL_OR_REQUIRED}}
```

**Validation Rules**: {{DESCRIBE_ANY_SPECIAL_VALIDATION}}

**Transformation Logic**: {{IF_DATA_IS_TRANSFORMED_BEFORE_STORAGE}}
```

# Structural Documentation Rules

- Skip and remove empty sections entirely.
- Use consistent semantic section patterns (what, how, tradeoffs, limitations).
- Reference concrete files, functions, or entry points where relevant.
- Prefer explicit file paths and function names over vague descriptions.

# Decision & Context Docs

Create auxiliary docs when complexity warrants:

- `decision-log.md`: Architectural or technical decisions with rationale.
- `changelog.md`: Human-readable summary of meaningful changes.

Keep these concise and factual; separate *what* from *why*.

# README Update Rules

- Update the “Last Updated” date to today’s date on every meaningful change in ISO formatting
- Ensure examples are copy-paste plausible and reflect current behavior.
- Document tradeoffs and constraints for major design choices.

# Maintenance Rules

- Keep README in sync with the codebase.
- Remove obsolete architecture diagrams or explanations immediately.
- Update diagrams when data flow or system boundaries change.
- Place a single README.md in the project root only.
- Avoid per-directory READMEs unless explicitly required.

# Development Workflow Integration

When generating or modifying code that affects project structure or behavior:

1. Check whether a README exists.
2. Determine if changes meet README update criteria.
3. Update documentation in the same response or commit.
4. Explicitly note when documentation was updated.

