# Link character thoughts after local extraction

Stage 2B records a required semantic Thought Subject but may leave event and fact IDs empty. Stage 3 retrieves time-compatible candidate Story Events and Event Facts, then uses an LLM to classify each update as linked, standalone, or unresolved; standalone topics are not forced onto events, and unresolved updates are withheld from Qdrant until reviewed.
