export function normalizeDetectionResponse(
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
            response.classifications
        )
    ) {
        return response;
    }

    /*
     * Workers AI native response shape.
     *
     * Example:
     *
     * {
     *   response: {
     *     classifications: [...]
     *   }
     * }
     *
     * or:
     *
     * {
     *   response: "{...}"
     * }
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
        return normalizeDetectionResponse(
            response.response
        );
    }

    /*
     * OpenAI-compatible chat completion
     * shape used by some Workers AI
     * models.
     *
     * Example:
     *
     * {
     *   choices: [
     *     {
     *       message: {
     *         content: "{...}"
     *       }
     *     }
     *   ]
     * }
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
     * Some providers may return the JSON
     * document itself as a string.
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