# Incremental commits

Commit each verified fix, completed approval-related change, and finished feature as a separate logical change. Make the commit as soon as the relevant checks pass, without waiting for a phase to end or for another commit request.

Use a brief subject that explains the change and its reason. Stage only the files or hunks belonging to that change. When a phase passes its acceptance checks, commit any remaining validation evidence and phase documentation together. If an approval or phase completion leaves the tree clean, report the existing commit that covers it.

After each incremental commit, push the current work branch to its matching branch on origin. Set upstream tracking on the first push. If the push is rejected, report the reason and preserve both histories.
