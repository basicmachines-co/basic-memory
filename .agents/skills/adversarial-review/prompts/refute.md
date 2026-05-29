# Refute the other reviewer

A different reviewer (a different model family) produced the findings below against the
same diff under review (the exact `git diff` command is provided with the findings). Your
job is to try to **refute** each one.

Default to skepticism: assume a finding is wrong until the code proves it right. A finding
that survives a genuine attempt to disprove it is worth far more than one nobody checked.

For each finding, read the actual code it points at and return a verdict:

- **refuted** — the claim is wrong, the code does not do what the finding says, the case
  cannot occur, or it is pure style with no correctness impact. Cite the specific code or
  fact that disproves it.
- **upheld** — you tried to refute it and could not; the finding is real.
- **partial** — the underlying issue is real but the finding mis-states the severity or
  scope. Explain, and set `corrected_severity` if the severity should change.

Do not be agreeable for its own sake, and do not refute for its own sake. Follow the code.

Return ONLY the structured verdicts object conforming to the provided schema. Every
verdict's `id` must match the `id` of the finding it judges.
