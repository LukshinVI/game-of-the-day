const LEADING_RELATION_MARKERS = [
    {
        pattern:
            /^потому\s+что(?:\s|$)/iu,

        type:
            'CAUSE'
    },

    {
        pattern:
            /^так\s+как(?:\s|$)/iu,

        type:
            'CAUSE'
    },

    {
        pattern:
            /^из-за\s+того\s+что(?:\s|$)/iu,

        type:
            'CAUSE'
    },

    {
        pattern:
            /^но(?:\s|$)/iu,

        type:
            'CONTRAST'
    },

    {
        pattern:
            /^а(?:\s|$)/iu,

        type:
            'CONTRAST'
    }
];

export function detectRelations(
    feedbackText,
    segments
) {
    /*
     * feedbackText remains part of the
     * public boundary intentionally.
     *
     * Relation detection itself operates
     * on the already extracted segments.
     *
     * We do not perform semantic
     * classification here.
     */
    void feedbackText;

    const relations = [];

    for (
        let index = 1;
        index < segments.length;
        index += 1
    ) {
        const previous =
            segments[index - 1];

        const current =
            segments[index];

        const marker =
            findLeadingRelationMarker(
                current.text
            );

        if (!marker) {
            continue;
        }

        if (
            marker.type ===
            'CAUSE'
        ) {
            relations.push({
                type:
                    'CAUSE',

                from_segment_id:
                    current.segment_id,

                to_segment_id:
                    previous.segment_id
            });

            continue;
        }

        if (
            marker.type ===
            'CONTRAST'
        ) {
            relations.push({
                type:
                    'CONTRAST',

                from_segment_id:
                    previous.segment_id,

                to_segment_id:
                    current.segment_id
            });
        }
    }

    return relations;
}

function findLeadingRelationMarker(
    segmentText
) {
    if (
        typeof segmentText !==
            'string'
    ) {
        return null;
    }

    const normalizedText =
        segmentText.trimStart();

    for (
        const marker of
        LEADING_RELATION_MARKERS
    ) {
        if (
            marker.pattern.test(
                normalizedText
            )
        ) {
            return marker;
        }
    }

    return null;
}