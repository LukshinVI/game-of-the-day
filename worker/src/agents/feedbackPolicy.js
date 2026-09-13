export function resolveFeedbackPolicy(
    signals
) {
    const negativeReaction =
        signals.find(
            (signal) =>
                isChildSignal(signal) &&
                signal.reaction_status ===
                    'CLEAR' &&
                signal.polarity ===
                    'NEGATIVE'
        );

    if (negativeReaction) {
        return {
            decision:
                'NEED_CLARIFICATION',

            clarification: {
                required: true,
                type:
                    'NEGATIVE_REASON',

                signal_id:
                    negativeReaction.signal_id
            }
        };
    }

    const ambiguousReaction =
        signals.find(
            (signal) =>
                isChildSignal(signal) &&
                signal.reaction_status ===
                    'AMBIGUOUS'
        );

    if (ambiguousReaction) {
        return {
            decision:
                'NEED_CLARIFICATION',

            clarification: {
                required: true,
                type:
                    'REACTION',

                signal_id:
                    ambiguousReaction.signal_id
            }
        };
    }

    const hasReaction =
        signals.some(
            (signal) =>
                isChildSignal(signal) &&
                signal.reaction_status ===
                    'CLEAR'
        );

    if (hasReaction) {
        return {
            decision:
                'OBSERVATION_RECORDED',

            clarification: {
                required: false,
                type: null,
                signal_id: null
            }
        };
    }

    return {
        decision:
            'NO_REACTION_EVIDENCE',

        clarification: {
            required: false,
            type: null,
            signal_id: null
        }
    };
}

function isChildSignal(signal) {
    return (
        signal.experiencer ===
            'CHILD' ||
        signal.experiencer ===
            'UNKNOWN'
    );
}