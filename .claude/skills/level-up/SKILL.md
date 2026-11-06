---
name: level-up
description: Actualiza y enriquece un archivo de context, skill o documentación existente que está desactualizado o incompleto. Reemplaza placeholders [TODO:] con datos reales, expande secciones vagas. Usar cuando un archivo tiene muchos [TODO:] o la realidad del negocio cambió. No inventa datos.
---

# Skill: level-up

## Description
Update and enrich an existing context file, skill, or process documentation that is outdated or incomplete. Transforms vague or placeholder-heavy files into specific, actionable references grounded in real business data.

## When to Use
- A context file contains too many `[TODO:]` entries
- A skill definition lacks clear process steps or decision logic
- Business reality has changed (new suppliers, new processes, new products)
- A file was written speculatively and needs real data filled in
- A skill produces inconsistent or shallow outputs

## Inputs Expected
- **File path**: Absolute path to the file to level up (required)
- **New information**: Specific data, values, names, processes to incorporate (provide as much as available)
- **Current context**: What has changed, what is now known, what prompted the update (optional but helpful)
- **Sections to target**: Specific sections to focus on (optional — if omitted, assess the whole file)

If inputs are missing, ask before proceeding. Do not invent data.

---

## Process

### Step 1: Read the Existing File
Read the target file in full. Note:
- Total number of `[TODO:]` placeholders
- Sections that are vague, speculative, or contradictory
- Information that may be stale (dates, prices, contacts, processes)
- Structure and format to preserve

### Step 2: Identify Gaps
Produce a short internal list of:
- What is missing that was explicitly flagged (`[TODO:]`)
- What is implicitly incomplete (e.g., a process with no steps, a contact with no name)
- What may be outdated based on current date or known context
- Structural gaps (sections that should exist but don't)

### Step 3: Ask for Missing Data (if not provided)
If the user did not supply data to fill specific gaps, ask targeted questions — one pass, grouped. Example:

> "To level up this file I need:
> 1. The current restock lead time from [Supplier]
> 2. The confirmed discount threshold for loyalty points
> 3. The name of the person responsible for [process]"

Do not proceed with invented values. Do not ask for data already visible in the file or in provided context.

### Step 4: Rewrite and Update
With real data in hand:
- Replace `[TODO:]` entries that now have answers
- Expand vague descriptions into specific, step-by-step language
- Update stale values (prices, contacts, dates, thresholds)
- Keep the original structure and section names intact
- Do not delete sections — consolidate if redundant, never remove
- If the file has grown significantly, note whether a section should be split into its own file

### Step 5: Flag Remaining Placeholders
For any `[TODO:]` that still lacks real data, keep the placeholder and add a brief note explaining what is needed and who might provide it. Example:

> `[TODO: confirm minimum order quantity with supplier — ask the owner or check the last invoice]`

### Step 6: Write the Updated File
Overwrite the original file with the improved version. Do not create a separate "v2" file unless explicitly asked.

---

## Output Expected
- Updated file at the original path
- Richer, more specific content
- Placeholders reduced by at least 50% (when data was provided)
- Summary of what changed: what was filled in, what's still missing, what was removed or restructured

---

## Quality Rules
- **Do not invent data.** If you don't know a value, keep the placeholder.
- **Do not delete placeholders without real data.** A placeholder is better than a wrong value.
- **Do not lose historical context.** If something changed, note the old value with a date rather than erasing it.
- **Preserve structure.** Same headings, same order, same format conventions.
- **Preserve terminology.** Don't rename things that other files or skills reference.
- **Write for humans.** Use plain language suited to whoever will use this file in the field.

---

## Completion Criteria
- File reads as more useful and specific than before
- `[TODO:]` entries reduced by at least 50% (if data was provided to fill them)
- No invented or fabricated data in the updated version
- All original sections retained
- File saved to the original path
- Changes summarized to the user for review
