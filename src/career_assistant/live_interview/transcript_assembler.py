"""按音频通道组装实时转写，阻止乱序 partial 覆盖 final。"""

from __future__ import annotations

from src.career_assistant.live_interview.contracts import AudioChannel, TranscriptEvent


class TranscriptAssembler:
    def __init__(self) -> None:
        self._last_sequence = {channel: -1 for channel in AudioChannel}
        self._final_sequences = {channel: set() for channel in AudioChannel}
        self._legacy_last_final: dict[AudioChannel, tuple[int, str]] = {}
        self._partial_sequences: dict[AudioChannel, int] = {}

    def has_newer_partial(self, event: TranscriptEvent) -> bool:
        return self._partial_sequences.get(event.channel, -1) > event.sequence

    def accept(self, event: TranscriptEvent) -> TranscriptEvent | None:
        last = self._last_sequence[event.channel]
        finals = self._final_sequences[event.channel]
        if event.provider in {"openai", "dashscope"}:
            # 上游用句子 ID 标识话语。OpenAI 两个 item 的最终结果可能反序抵达。
            if event.sequence in finals or (not event.is_final and event.sequence < last):
                return None
            if event.is_final:
                finals.add(event.sequence)
        else:
            # 旧适配器用音频帧号，同一帧之后可能返回不同的句子。
            prior = self._legacy_last_final.get(event.channel)
            if event.sequence < last or prior == (event.sequence, event.text):
                return None
            if prior and event.sequence == prior[0] and not event.is_final:
                return None
            if event.is_final:
                self._legacy_last_final[event.channel] = (event.sequence, event.text)
        self._last_sequence[event.channel] = max(last, event.sequence)
        if not event.is_final:
            self._partial_sequences[event.channel] = event.sequence
        elif not self.has_newer_partial(event):
            self._partial_sequences.pop(event.channel, None)
        return event
