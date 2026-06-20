from __future__ import annotations

import base64
import mimetypes
import io


def encode_image_as_base64(f: io.BytesIO | io.BufferedReader, filename: str, default_mine: str | None = None) -> str:
    if 'b' not in f.mode:
        raise ValueError("File-like object must be opened in binary mode.")

    # Get the MIME type based on the file extension
    mime_type, _ = mimetypes.guess_type(filename)
    if mime_type is None:
        if default_mine is None:
            raise ValueError(f"Could not determine MIME type for file: {filename}")
        mime_type = default_mine

    # Read the file content and encode it as base64
    f.seek(0)  # Ensure we're at the start of the file
    encoded_string = base64.b64encode(f.read()).decode('utf-8')

    # Return the data URI scheme string
    return f"data:{mime_type};base64,{encoded_string}"


def encode_image_file_as_base64(file_path: str, default_mine: str | None = None) -> str:
    with open(file_path, 'rb') as f:
        return encode_image_as_base64(f, file_path, default_mine)
