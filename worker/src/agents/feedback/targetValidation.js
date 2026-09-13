export function validateTargetResult(
    result,
    signalContexts
) {
    if (
        !result ||
        typeof result !== 'object' ||
        Array.isArray(result) ||
        !Array.isArray(
            result.links
        )
    ) {
        throw new Error(
            'TARGET_RESULT_INVALID'
        );
    }

    if (
        result.links.length !==
        signalContexts.length
    ) {
        throw new Error(
            'TARGET_LINK_COUNT_INVALID'
        );
    }

    const contextsBySignalId =
        new Map(
            signalContexts.map(
                (context) => [
                    context.signal.signal_id,
                    context
                ]
            )
        );

    const returnedIds =
        new Set();

    const normalizedLinks =
        [];

    for (
        const link of
        result.links
    ) {
        normalizedLinks.push(
            validateAndNormalizeLink(
                link,
                contextsBySignalId,
                returnedIds
            )
        );
    }

    return {
        ...result,

        links:
            normalizedLinks
    };
}

function validateAndNormalizeLink(
    link,
    contextsBySignalId,
    returnedIds
) {
    if (
        !link ||
        typeof link !== 'object' ||
        Array.isArray(link)
    ) {
        throw new Error(
            'TARGET_LINK_INVALID'
        );
    }

    const context =
        contextsBySignalId.get(
            link.signal_id
        );

    /*
     * Unknown signal_id is a structural
     * contract failure.
     *
     * We cannot know which real signal
     * this link belongs to, so we must
     * not silently repair it.
     */
    if (!context) {
        throw new Error(
            'TARGET_SIGNAL_ID_INVALID'
        );
    }

    /*
     * Duplicate signal IDs are also
     * structural corruption because
     * another expected signal would then
     * have no trustworthy link.
     */
    if (
        returnedIds.has(
            link.signal_id
        )
    ) {
        throw new Error(
            'TARGET_SIGNAL_DUPLICATE'
        );
    }

    returnedIds.add(
        link.signal_id
    );

    const validCandidateIds =
        new Set(
            context.candidates.map(
                (candidate) =>
                    candidate.candidate_id
            )
        );

    return normalizeCandidateSelection(
        link,
        validCandidateIds
    );
}

function normalizeCandidateSelection(
    link,
    validCandidateIds
) {
    /*
     * From this point onward the signal
     * itself is known and trustworthy.
     *
     * Failures concern only semantic
     * target selection.
     *
     * Target uncertainty must therefore
     * degrade to UNSPECIFIED instead of
     * destroying the complete feedback
     * analysis.
     */

    if (
        !Array.isArray(
            link.candidate_ids
        )
    ) {
        return downgradeToUnspecified(
            link,
            'TARGET_CANDIDATES_INVALID'
        );
    }

    const uniqueIds =
        new Set(
            link.candidate_ids
        );

    if (
        uniqueIds.size !==
        link.candidate_ids.length
    ) {
        return downgradeToUnspecified(
            link,
            'TARGET_CANDIDATE_DUPLICATE'
        );
    }

    for (
        const candidateId of
        link.candidate_ids
    ) {
        if (
            typeof candidateId !==
                'string' ||
            !validCandidateIds.has(
                candidateId
            )
        ) {
            return downgradeToUnspecified(
                link,
                'TARGET_CANDIDATE_ID_INVALID'
            );
        }
    }

    if (
        ![
            'RESOLVED',
            'UNSPECIFIED',
            'AMBIGUOUS'
        ].includes(
            link.status
        )
    ) {
        return downgradeToUnspecified(
            link,
            'TARGET_STATUS_INVALID'
        );
    }

    if (
        link.status ===
            'RESOLVED' &&
        link.candidate_ids.length !==
            1
    ) {
        return downgradeToUnspecified(
            link,
            'TARGET_RESOLVED_INVALID'
        );
    }

    if (
        link.status ===
            'UNSPECIFIED' &&
        link.candidate_ids.length !==
            0
    ) {
        return downgradeToUnspecified(
            link,
            'TARGET_UNSPECIFIED_INVALID'
        );
    }

    if (
        link.status ===
            'AMBIGUOUS' &&
        link.candidate_ids.length <
            2
    ) {
        return downgradeToUnspecified(
            link,
            'TARGET_AMBIGUOUS_INVALID'
        );
    }

    return {
        signal_id:
            link.signal_id,

        status:
            link.status,

        candidate_ids:
            link.candidate_ids
    };
}

function downgradeToUnspecified(
    link,
    reason
) {
    /*
     * Useful operational signal:
     * we preserve the feedback,
     * but can still observe how often
     * the model violates target
     * constraints.
     */
    console.warn(
        'TARGET_LINK_DOWNGRADED',
        JSON.stringify({
            signal_id:
                link.signal_id,

            reason,

            original_status:
                link.status ?? null,

            original_candidate_ids:
                Array.isArray(
                    link.candidate_ids
                )
                    ? link.candidate_ids
                    : null
        })
    );

    return {
        signal_id:
            link.signal_id,

        status:
            'UNSPECIFIED',

        candidate_ids:
            []
    };
}