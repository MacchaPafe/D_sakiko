# Separate character-thought extraction from document extraction

Character Thought Update and Event Fact candidates are extracted in an independent Stage 2B after the existing Stage 2A has produced Story Event candidates. This keeps objective event extraction separate from subjective character cognition, allows either pass to be reviewed and rerun independently, and avoids making one LLM response simultaneously resolve events, relations, lore, facts, and evolving viewpoints.
