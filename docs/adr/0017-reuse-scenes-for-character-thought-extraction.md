# Reuse scenes for character-thought extraction

Stage 2B reuses each complete Stage 2 scene and requires every extracted fact or thought update to cite local evidence IDs. It restores speaker confidence and inner-monologue metadata, treats scene-level present characters only as candidates, and falls back to overlapping utterance windows only when a rendered prompt exceeds a configured limit.
