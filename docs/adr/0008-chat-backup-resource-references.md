# Chat backups use resource references

Chat backup packages store message resource fields as backup resource references instead of runtime file paths, and import rewrites those references into local relative paths under `reference_audio`. This keeps the exported chat data independent of the original machine and lets import decide whether to reuse, copy, rename, or drop each resource based on the backup manifest and local files.
