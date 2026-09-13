import {
    splitClauses
} from './splitClauses.js';

import {
    detectRelations
} from './detectRelations.js';

export function segmentFeedback(
    feedbackText
) {
    const clauses =
        splitClauses(
            feedbackText
        );

    const segments =
        clauses.map(
            (
                clause,
                index
            ) => ({
                segment_id:
                    `segment-${index + 1}`,

                text:
                    clause.text,

                start_offset:
                    clause.start_offset,

                end_offset:
                    clause.end_offset
            })
        );

    const relations =
        detectRelations(
            feedbackText,
            segments
        );

    return {
        segments,
        relations
    };
}