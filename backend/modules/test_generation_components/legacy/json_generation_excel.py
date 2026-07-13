from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .json_generation_dependencies import _convert_json_to_excel_adapter


class LegacyGenerationExcelMixin:
    def generate_test_cases_excel(
        self,
        requirement: str,
        project_id: int,
        db: Session = None,
        doc_type: str = "requirement",
        compress: bool = False,
        user_id: int = None,
        current_biz_key: str = "",
        only_current_biz: bool = False,
        multi_pass: bool = True,
        generation_mode: str = "",
    ) -> bytes:
        json_result = self.generate_test_cases_json(
            requirement,
            project_id,
            db,
            doc_type,
            compress,
            user_id=user_id,
            current_biz_key=current_biz_key,
            only_current_biz=only_current_biz,
            multi_pass=multi_pass,
            generation_mode=generation_mode,
        )
        return self.convert_json_to_excel(json_result)

    def convert_json_to_excel(
        self,
        json_data: list | dict,
        *,
        include_internal_fields: bool = False,
    ) -> bytes:
        """Convert generated JSON cases into an Excel workbook payload."""
        return _convert_json_to_excel_adapter(
            json_data,
            include_internal_fields=include_internal_fields,
        )
