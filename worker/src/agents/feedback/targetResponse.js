export function normalizeTargetResponse(
    response
) {
    if (
        response === null ||
        response === undefined
    ) {
        return null;
    }

    /*
     * Already normalized object.
     */
    if (
        typeof response ===
            'object' &&
        !Array.isArray(
            response
        ) &&
        Array.isArray(
            response.links
        )
    ) {
        return response;
    }

    /*
     * Workers AI native response envelope.
     */
    if (
        typeof response ===
            'object' &&
        !Array.isArray(
            response
        ) &&
        Object.prototype.hasOwnProperty.call(
            response,
            'response'
        )
    ) {
        return normalizeTargetResponse(
            response.response
        );
    }

    /*
     * OpenAI-compatible chat completion
     * envelope used by Gemma.
     */
    const chatContent =
        response?.choices?.[0]
            ?.message?.content;

    if (
        typeof chatContent ===
        'string'
    ) {
        return parseJsonObject(
            chatContent
        );
    }

    /*
     * Some providers may return
     * the JSON document directly
     * as a string.
     */
    if (
        typeof response ===
        'string'
    ) {
        return parseJsonObject(
            response
        );
    }

    return null;
}

function parseJsonObject(
    value
) {
    try {
        const parsed =
            JSON.parse(
                value
            );

        if (
            !parsed ||
            typeof parsed !==
                'object' ||
            Array.isArray(
                parsed
            )
        ) {
            return null;
        }

        return parsed;
    } catch {
        return null;
    }
}