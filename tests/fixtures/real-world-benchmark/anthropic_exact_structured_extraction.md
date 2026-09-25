    response = _client().messages.create(
        model=EXTRACTOR_MODEL,
        max_tokens=4096,
        system=EXTRACTOR_SYSTEM,
        output_config={
            "format": {
                "type": "json_schema",
                "schema": build_extraction_schema(schema, derived_fields),
            }
        },
