# Store self-contained character-thought payloads

Each `character_thoughts` point stores a flat, self-contained role-safe thought text, temporal interval, semantic subject links, Thought Thread, Epistemic Status, and minimal evidence and confidence metadata. Qdrant indexes only fields used for filtering, while provenance remains in the payload so a retrieved thought can be reviewed without joining offline artifacts.
