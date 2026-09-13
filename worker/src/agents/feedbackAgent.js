import {
    detectSignals
} from './feedback/detectSignals.js';

import {
    linkTargets
} from './feedback/linkTargets.js';

import {
    resolveFeedbackPolicy
} from './feedbackPolicy.js';

const FEEDBACK_MODEL =
    '@cf/google/gemma-4-26b-a4b-it';

export async function analyzeFeedback(
    env,
    input
) {
    const feedbackText =
        input?.feedback?.text;

    if (
        typeof feedbackText !==
            'string' ||
        feedbackText.trim().length ===
            0
    ) {
        throw new Error(
            'FEEDBACK_TEXT_REQUIRED'
        );
    }

    let modelCalls = 0;

    /*
     * Считаем именно фактические обращения
     * к AI binding.
     *
     * Это важнее, чем считать логические
     * стадии пайплайна:
     *
     * Stage 2 может быть вызван как модуль,
     * но внутри решить, что target candidates
     * отсутствуют, и вообще не обращаться
     * к модели.
     */
    const countedEnv =
        createCountedAiEnv(
            env,
            () => {
                modelCalls += 1;
            }
        );

    const detectedSignals =
        await detectSignals(
            countedEnv,
            FEEDBACK_MODEL,
            feedbackText
        );

    let signals =
        detectedSignals.map(
            (signal, index) => ({
                signal_id:
                    `signal-${index + 1}`,

                ...signal,

                target:
                    defaultTargetForSignal(
                        signal
                    )
            })
        );

    const linkableSignals =
        signals.filter(
            shouldLinkTarget
        );

    if (
        linkableSignals.length > 0
    ) {
        signals =
            await linkTargets(
                countedEnv,
                FEEDBACK_MODEL,
                feedbackText,
                signals,
                linkableSignals
            );
    }

    const policy =
        resolveFeedbackPolicy(
            signals
        );

    return {
        version:
            'feedback-v2',

        analysis_status:
            'COMPLETE',

        signals,

        decision:
            policy.decision,

        clarification:
            policy.clarification,

        meta: {
            model:
                FEEDBACK_MODEL,

            model_calls:
                modelCalls
        }
    };
}

function createCountedAiEnv(
    env,
    onCall
) {
    if (
        !env ||
        !env.AI ||
        typeof env.AI.run !==
            'function'
    ) {
        throw new Error(
            'AI_BINDING_REQUIRED'
        );
    }

    return {
        ...env,

        AI: {
            async run(
                ...args
            ) {
                onCall();

                return env.AI.run(
                    ...args
                );
            }
        }
    };
}

function shouldLinkTarget(
    signal
) {
    /*
     * Target linking нужен только тогда,
     * когда реакция уже семантически
     * определена.
     *
     * AMBIGUOUS reaction сначала требует
     * clarification. До ответа пользователя
     * target-linking не даёт продуктовой
     * ценности и только расходует inference.
     */
    if (
        signal.reaction_status !==
            'CLEAR'
    ) {
        return false;
    }

    if (
        ![
            'CHILD',
            'UNKNOWN'
        ].includes(
            signal.experiencer
        )
    ) {
        return false;
    }

    if (
        ![
            'EXPLICIT_EVALUATION',
            'AFFECT',
            'VOLUNTARY_ENGAGEMENT'
        ].includes(
            signal.evidence_type
        )
    ) {
        return false;
    }

    return true;
}

function defaultTargetForSignal(
    signal
) {
    if (
        signal.reaction_status ===
            'AMBIGUOUS'
    ) {
        return {
            status:
                'NOT_APPLICABLE',

            target_text:
                null,

            candidate_target_texts:
                []
        };
    }

    if (
        signal.reaction_status ===
            'NONE'
    ) {
        return {
            status:
                'NOT_APPLICABLE',

            target_text:
                null,

            candidate_target_texts:
                []
        };
    }

    return {
        status:
            'UNSPECIFIED',

        target_text:
            null,

        candidate_target_texts:
            []
    };
}