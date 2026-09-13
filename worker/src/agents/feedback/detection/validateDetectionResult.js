export function validateDetectionResult(
    result,
    segments,
    relations
) {
    if (
        !result ||
        typeof result !==
            'object' ||
        Array.isArray(result) ||
        !Array.isArray(
            result.classifications
        )
    ) {
        throw new Error(
            'DETECTION_RESULT_INVALID'
        );
    }

    if (
        result.classifications.length !==
        segments.length
    ) {
        throw new Error(
            'DETECTION_CLASSIFICATION_COUNT_INVALID'
        );
    }

    const segmentIds =
        new Set(
            segments.map(
                (segment) =>
                    segment.segment_id
            )
        );

    const causeSegmentIds =
        new Set(
            relations
                .filter(
                    (relation) =>
                        relation.type ===
                        'CAUSE'
                )
                .map(
                    (relation) =>
                        relation
                            .from_segment_id
                )
        );

    const seenIds =
        new Set();

    for (
        const classification of
        result.classifications
    ) {
        validateClassification(
            classification,
            segmentIds,
            causeSegmentIds,
            seenIds
        );
    }

    for (
        const segmentId of
        segmentIds
    ) {
        if (
            !seenIds.has(
                segmentId
            )
        ) {
            throw new Error(
                'DETECTION_SEGMENT_MISSING'
            );
        }
    }
}

function validateClassification(
    classification,
    segmentIds,
    causeSegmentIds,
    seenIds
) {
    if (
        !classification ||
        typeof classification !==
            'object' ||
        Array.isArray(
            classification
        )
    ) {
        throw new Error(
            'DETECTION_CLASSIFICATION_INVALID'
        );
    }

    const segmentId =
        classification.segment_id;

    if (
        typeof segmentId !==
            'string' ||
        !segmentIds.has(
            segmentId
        )
    ) {
        throw new Error(
            'DETECTION_SEGMENT_ID_INVALID'
        );
    }

    if (
        seenIds.has(
            segmentId
        )
    ) {
        throw new Error(
            'DETECTION_SEGMENT_DUPLICATE'
        );
    }

    seenIds.add(
        segmentId
    );

    validateReactionContract(
        classification
    );

    validateEngagementContract(
        classification
    );

    validateCauseContract(
        classification,
        causeSegmentIds.has(
            segmentId
        )
    );
}

function validateReactionContract(
    classification
) {
    const {
        evidence_type:
            evidenceType,

        reaction_status:
            reactionStatus,

        polarity
    } =
        classification;

    if (
        evidenceType ===
            'BEHAVIOR_ONLY' &&
        (
            reactionStatus !==
                'NONE' ||
            polarity !==
                'NONE'
        )
    ) {
        throw new Error(
            'BEHAVIOR_REACTION_INVALID'
        );
    }

    if (
        reactionStatus ===
            'NONE' &&
        polarity !==
            'NONE'
    ) {
        throw new Error(
            'REACTION_POLARITY_INVALID'
        );
    }

    if (
        reactionStatus ===
            'AMBIGUOUS' &&
        polarity !==
            'NONE'
    ) {
        throw new Error(
            'AMBIGUOUS_POLARITY_INVALID'
        );
    }

    if (
        reactionStatus ===
            'CLEAR' &&
        polarity ===
            'NONE'
    ) {
        throw new Error(
            'CLEAR_POLARITY_INVALID'
        );
    }

    if (
        [
            'EXPLICIT_EVALUATION',
            'AFFECT',
            'VOLUNTARY_ENGAGEMENT'
        ].includes(
            evidenceType
        ) &&
        reactionStatus ===
            'NONE'
    ) {
        throw new Error(
            'SEMANTIC_REACTION_STATUS_INVALID'
        );
    }
}

function validateEngagementContract(
    classification
) {
    const voluntary =
        classification
            .evidence_type ===
        'VOLUNTARY_ENGAGEMENT';

    if (
        voluntary &&
        classification
            .engagement_basis ===
            'NONE'
    ) {
        throw new Error(
            'ENGAGEMENT_BASIS_REQUIRED'
        );
    }

    if (
        !voluntary &&
        classification
            .engagement_basis !==
            'NONE'
    ) {
        throw new Error(
            'ENGAGEMENT_BASIS_NOT_APPLICABLE'
        );
    }
}

function validateCauseContract(
    classification,
    isCauseSegment
) {
    if (
        isCauseSegment &&
        classification
            .cause_type ===
            'NOT_APPLICABLE'
    ) {
        throw new Error(
            'CAUSE_TYPE_REQUIRED'
        );
    }

    if (
        !isCauseSegment &&
        classification
            .cause_type !==
            'NOT_APPLICABLE'
    ) {
        throw new Error(
            'CAUSE_TYPE_NOT_APPLICABLE'
        );
    }
}