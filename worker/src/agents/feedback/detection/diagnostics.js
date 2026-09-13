export function logDetectionFailure(
    error,
    response,
    normalizedResult
) {
    const rawResponse =
        response?.response ??
        null;

    console.error(
        'FEEDBACK_DETECTION_FAILURE',
        JSON.stringify({
            error:
                error instanceof Error
                    ? error.message
                    : String(error),

            finish_reason:
                response?.finish_reason ??
                null,

            raw_response_length:
                typeof rawResponse ===
                    'string'
                    ? rawResponse.length
                    : null,

            /*
             * The model is allowed to output
             * candidate IDs and semantic enums,
             * not original feedback text.
             *
             * Keeping only the tail is enough
             * to diagnose truncated JSON while
             * avoiding unnecessarily large logs.
             */
            raw_response_tail:
                typeof rawResponse ===
                    'string'
                    ? rawResponse.slice(
                        -800
                    )
                    : null,

            normalized_result:
                normalizedResult
        })
    );
}