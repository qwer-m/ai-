from typing import Iterator
import uuid

from sqlalchemy.orm import Session

from core.ai.ai_client import get_client_for_user
from modules.testing.test_generation_components.legacy.stream.batches import (
    LegacyGenerationStreamBatchesMixin,
)
from modules.testing.test_generation_components.legacy.stream.persist import (
    LegacyGenerationStreamPersistMixin,
)
from modules.testing.test_generation_components.legacy.stream.prepare import (
    LegacyGenerationStreamPrepareMixin,
)


class LegacyGenerationStreamGenerationMixin(
    LegacyGenerationStreamPrepareMixin,
    LegacyGenerationStreamBatchesMixin,
    LegacyGenerationStreamPersistMixin,
):

    def generate_test_cases_stream(
        self,
        requirement: str,
        project_id: int,
        db: Session = None,
        doc_type: str = "requirement",
        compress: bool = False,
        expected_count: int = 20,
        batch_size: int = 10,
        overwrite: bool = False,
        append: bool = False,
        user_id: int = None,
        current_biz_key: str = "",
        only_current_biz: bool = False,
        multi_pass: bool = True,
        generation_mode: str = "",
    ) -> Iterator[str]:
        client = get_client_for_user(user_id, db)
        request_id = uuid.uuid4().hex

        state = yield from self._stream_prepare_phase(
            client=client,
            request_id=request_id,
            requirement=requirement,
            project_id=project_id,
            db=db,
            doc_type=doc_type,
            compress=compress,
            expected_count=expected_count,
            batch_size=batch_size,
            overwrite=overwrite,
            append=append,
            user_id=user_id,
            current_biz_key=current_biz_key,
            only_current_biz=only_current_biz,
            multi_pass=multi_pass,
            generation_mode=generation_mode,
        )
        if not isinstance(state, dict) or state.get("abort"):
            return

        state = yield from self._stream_run_batches_phase(state=state)
        if not isinstance(state, dict):
            return

        yield from self._stream_persist_phase(state=state)
