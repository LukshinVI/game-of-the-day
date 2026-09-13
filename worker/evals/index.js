import {
    runFeedbackEvals
} from './runFeedbackEvals.js';

const MODEL_OVERRIDES = {
    baseline:
        null,

    gemma:
        '@cf/google/gemma-4-26b-a4b-it',

    glm:
        '@cf/zai-org/glm-4.7-flash'
};

const AI_CALL_TIMEOUT_MS =
    120000;

export default {
    async fetch(
        request,
        env
    ) {
        const url =
            new URL(
                request.url
            );

        if (
            url.pathname !==
            '/__evals/feedback'
        ) {
            return new Response(
                'Not found',
                {
                    status:
                        404
                }
            );
        }

        const caseId =
            url.searchParams.get(
                'case'
            );

        const modelKey =
            url.searchParams.get(
                'model'
            ) ||
            'baseline';

        if (
            !Object.prototype.hasOwnProperty.call(
                MODEL_OVERRIDES,
                modelKey
            )
        ) {
            return Response.json(
                {
                    error:
                        'UNKNOWN_EVAL_MODEL',

                    supported_models:
                        Object.keys(
                            MODEL_OVERRIDES
                        )
                },
                {
                    status:
                        400
                }
            );
        }

        const modelOverride =
            MODEL_OVERRIDES[
                modelKey
            ];

        const diagnostics = {
            ai_calls: []
        };

        const evalEnv =
            modelOverride ===
            null
                ? env
                : createEvalEnvWithModelOverride(
                    env,
                    modelOverride,
                    diagnostics
                );

        console.log(
            '[EVAL] START',
            {
                case_id:
                    caseId,
                model:
                    modelKey,
                model_override:
                    modelOverride
            }
        );

        try {
            const results =
                await runFeedbackEvals(
                    evalEnv,
                    caseId
                );

            console.log(
                '[EVAL] END',
                {
                    case_id:
                        caseId,
                    model:
                        modelKey,
                    ai_calls:
                        diagnostics.ai_calls.length
                }
            );

            return Response.json({
                model:
                    modelKey,

                model_override:
                    modelOverride,

                diagnostics,

                results
            });
        } catch (error) {
            console.error(
                '[EVAL] ERROR',
                {
                    case_id:
                        caseId,
                    model:
                        modelKey,
                    message:
                        error.message
                }
            );

            return Response.json(
                {
                    model:
                        modelKey,

                    model_override:
                        modelOverride,

                    error:
                        error.message,

                    diagnostics
                },
                {
                    status:
                        400
                }
            );
        }
    }
};

function createEvalEnvWithModelOverride(
    env,
    modelOverride,
    diagnostics
) {
    let callSequence =
        0;

    return {
        ...env,

        AI: {
            ...env.AI,

            async run(
                originalModel,
                options
            ) {
                callSequence +=
                    1;

                const callId =
                    callSequence;

                const startedAt =
                    Date.now();

                console.log(
                    '[EVAL AI] START',
                    {
                        call_id:
                            callId,
                        requested_model:
                            originalModel,
                        actual_model:
                            modelOverride
                    }
                );

                try {
                    const response =
                        await runWithTimeout(
                            () =>
                                env.AI.run(
                                    modelOverride,
                                    options
                                ),
                            AI_CALL_TIMEOUT_MS,
                            callId
                        );

                    const durationMs =
                        Date.now() -
                        startedAt;

                    console.log(
                        '[EVAL AI] END',
                        {
                            call_id:
                                callId,
                            duration_ms:
                                durationMs,
                            requested_model:
                                originalModel,
                            actual_model:
                                modelOverride
                        }
                    );

                    diagnostics.ai_calls.push({
                        call_id:
                            callId,

                        duration_ms:
                            durationMs,

                        status:
                            'SUCCESS',

                        requested_model:
                            originalModel,

                        actual_model:
                            modelOverride,

                        raw_response:
                            response
                    });

                    return response;
                } catch (error) {
                    const durationMs =
                        Date.now() -
                        startedAt;

                    const status =
                        error.code ===
                        'MODEL_TIMEOUT'
                            ? 'TIMEOUT'
                            : 'ERROR';

                    console.error(
                        '[EVAL AI] ERROR',
                        {
                            call_id:
                                callId,
                            duration_ms:
                                durationMs,
                            status,
                            requested_model:
                                originalModel,
                            actual_model:
                                modelOverride,
                            message:
                                error.message
                        }
                    );

                    diagnostics.ai_calls.push({
                        call_id:
                            callId,

                        duration_ms:
                            durationMs,

                        status,

                        requested_model:
                            originalModel,

                        actual_model:
                            modelOverride,

                        error:
                            error.message
                    });

                    throw error;
                }
            }
        }
    };
}

async function runWithTimeout(
    operation,
    timeoutMs,
    callId
) {
    let timeoutId;

    const timeoutPromise =
        new Promise(
            (
                resolve,
                reject
            ) => {
                void resolve;

                timeoutId =
                    setTimeout(
                        () => {
                            const error =
                                new Error(
                                    `AI call ${callId} timed out after ${timeoutMs} ms`
                                );

                            error.code =
                                'MODEL_TIMEOUT';

                            reject(
                                error
                            );
                        },
                        timeoutMs
                    );
            }
        );

    try {
        return await Promise.race([
            operation(),
            timeoutPromise
        ]);
    } finally {
        clearTimeout(
            timeoutId
        );
    }
}