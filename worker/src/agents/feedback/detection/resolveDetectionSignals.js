export function resolveDetectionSignals(
    classifications,
    segments,
    relations
) {
    const segmentById =
        new Map(
            segments.map(
                (segment) => [
                    segment.segment_id,
                    segment
                ]
            )
        );

    const classificationById =
        new Map(
            classifications.map(
                (
                    classification
                ) => [
                    classification
                        .segment_id,

                    classification
                ]
            )
        );

    const causeRelationByTarget =
        buildCauseRelationMap(
            relations
        );

    const causeSourceIds =
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

    const signals = [];

    for (
        const segment of
        segments
    ) {
        const classification =
            classificationById.get(
                segment.segment_id
            );

        if (!classification) {
            continue;
        }

        /*
         * A pure BEHAVIOR_ONLY segment that
         * exists only as the structural cause
         * of another reaction is provenance
         * for that reaction's reason.
         *
         * It does not need to become a separate
         * product-level signal.
         */
        if (
            isPureCauseSegment(
                classification,
                causeSourceIds.has(
                    segment.segment_id
                )
            )
        ) {
            continue;
        }

        const {
            negativeReason,
            reasonText
        } =
            resolveNegativeReason(
                classification,
                causeRelationByTarget,
                segmentById,
                classificationById
            );

        signals.push({
            evidence_type:
                classification
                    .evidence_type,

            reaction_status:
                classification
                    .reaction_status,

            polarity:
                classification
                    .polarity,

            experiencer:
                classification
                    .experiencer,

            evidence_text:
                segment.text,

            engagement_basis:
                classification
                    .engagement_basis,

            negative_reason:
                negativeReason,

            reason_text:
                reasonText
        });
    }

    return signals;
}

function buildCauseRelationMap(
    relations
) {
    const result =
        new Map();

    for (
        const relation of
        relations
    ) {
        if (
            relation.type !==
            'CAUSE'
        ) {
            continue;
        }

        /*
         * Current deterministic segmenter
         * produces one direct cause relation
         * per target segment.
         *
         * If this changes later, selection
         * must become explicit rather than
         * silently choosing among causes.
         */
        if (
            !result.has(
                relation
                    .to_segment_id
            )
        ) {
            result.set(
                relation
                    .to_segment_id,
                relation
            );
        }
    }

    return result;
}

function resolveNegativeReason(
    classification,
    causeRelationByTarget,
    segmentById,
    classificationById
) {
    const clearNegative =
        classification
            .reaction_status ===
            'CLEAR' &&
        classification
            .polarity ===
            'NEGATIVE';

    if (!clearNegative) {
        return {
            negativeReason:
                'NOT_APPLICABLE',

            reasonText:
                null
        };
    }

    const relation =
        causeRelationByTarget.get(
            classification.segment_id
        );

    if (!relation) {
        return {
            negativeReason:
                'UNSPECIFIED',

            reasonText:
                null
        };
    }

    const causeSegment =
        segmentById.get(
            relation.from_segment_id
        );

    const causeClassification =
        classificationById.get(
            relation.from_segment_id
        );

    if (
        !causeSegment ||
        !causeClassification
    ) {
        return {
            negativeReason:
                'UNSPECIFIED',

            reasonText:
                null
        };
    }

    const causeType =
        causeClassification
            .cause_type;

    if (
        causeType ===
            'NOT_APPLICABLE'
    ) {
        return {
            negativeReason:
                'UNSPECIFIED',

            reasonText:
                null
        };
    }

    return {
        negativeReason:
            causeType,

        reasonText:
            causeSegment.text
    };
}

function isPureCauseSegment(
    classification,
    isCauseSource
) {
    if (!isCauseSource) {
        return false;
    }

    return (
        classification
            .evidence_type ===
            'BEHAVIOR_ONLY' &&
        classification
            .reaction_status ===
            'NONE' &&
        classification
            .polarity ===
            'NONE'
    );
}