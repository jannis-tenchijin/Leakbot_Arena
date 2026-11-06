"""Minimal retro UI widgets: buttons, a slider and a progress bar."""

from __future__ import annotations

from collections.abc import Callable

import pygame

from . import theme


class Button:
    """Clickable button. ``label`` may be a callable so toggles can show their state."""

    def __init__(
        self,
        rect: pygame.Rect,
        label: str | Callable[[], str],
        on_click: Callable[[], None],
        enabled: Callable[[], bool] = lambda: True,
        accent: tuple[int, int, int] = theme.ACCENT,
        size: int = 13,
    ) -> None:
        self.rect = rect
        self._label = label
        self.on_click = on_click
        self.enabled = enabled
        self.accent = accent
        self.size = size
        self._pressed = False

    @property
    def label(self) -> str:
        return self._label() if callable(self._label) else self._label

    def handle(self, event: pygame.event.Event) -> bool:
        """Process a mouse event; returns True if it was consumed."""
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.rect.collidepoint(event.pos):
            self._pressed = self.enabled()
            return True
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self._pressed:
            self._pressed = False
            if self.rect.collidepoint(event.pos) and self.enabled():
                self.on_click()
            return True
        return False

    def draw(self, surf: pygame.Surface) -> None:
        enabled = self.enabled()
        hover = enabled and self.rect.collidepoint(pygame.mouse.get_pos())
        offset = 2 if self._pressed else 0
        body = self.rect.move(0, offset)
        # Drop shadow, body, border: chunky arcade button
        if not self._pressed:
            pygame.draw.rect(surf, (6, 6, 18), self.rect.move(0, 3), border_radius=6)
        fill = (52, 56, 112) if hover else theme.PANEL
        pygame.draw.rect(surf, fill, body, border_radius=6)
        border = self.accent if enabled else theme.PANEL_EDGE
        pygame.draw.rect(surf, border, body, width=2, border_radius=6)
        text = theme.font(self.size, bold=True).render(self.label, True, theme.TEXT if enabled else theme.MUTED)
        surf.blit(text, text.get_rect(center=body.center))


class Switch:
    """Segmented switch with one segment per option, e.g. EN | 日本語."""

    def __init__(
        self,
        rect: pygame.Rect,
        options: list[tuple[str, str]],
        get: Callable[[], str],
        set_: Callable[[str], None],
    ) -> None:
        self.rect = rect
        self.options = options  # (value, label)
        self.get = get
        self.set = set_

    def _segments(self) -> list[pygame.Rect]:
        w = self.rect.w // len(self.options)
        return [pygame.Rect(self.rect.x + i * w, self.rect.y, w, self.rect.h) for i in range(len(self.options))]

    def handle(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.rect.collidepoint(event.pos):
            for (value, _), seg in zip(self.options, self._segments()):
                if seg.collidepoint(event.pos):
                    self.set(value)
            return True
        if event.type == pygame.MOUSEBUTTONUP and self.rect.collidepoint(event.pos):
            return True  # swallow the matching release so nothing underneath reacts
        return False

    def draw(self, surf: pygame.Surface) -> None:
        pygame.draw.rect(surf, (6, 6, 18), self.rect.move(0, 3), border_radius=6)
        pygame.draw.rect(surf, theme.PANEL, self.rect, border_radius=6)
        current = self.get()
        for (value, label), seg in zip(self.options, self._segments()):
            active = value == current
            if active:
                pygame.draw.rect(surf, theme.ACCENT, seg.inflate(-4, -4), border_radius=4)
            # The Japanese font has Latin glyphs too, so both labels render in either language
            img = theme.font(12, bold=True, japanese=True).render(label, True, (14, 16, 38) if active else theme.MUTED)
            surf.blit(img, img.get_rect(center=seg.center))
        pygame.draw.rect(surf, theme.ACCENT, self.rect, width=2, border_radius=6)


_NO_LINE_START = set("、。,.)」』!?:・ー")  # Japanese punctuation that must not begin a line


def wrap_text(font: pygame.font.Font, text: str, width: int) -> list[str]:
    """Split text into lines that fit width: by words for spaced text, by characters for Japanese."""
    if " " in text and not any(ord(c) > 0x3000 for c in text):
        lines, current = [], ""
        for word in text.split(" "):
            candidate = f"{current} {word}" if current else word
            if current and font.size(candidate)[0] > width:
                lines.append(current)
                current = word
            else:
                current = candidate
        return lines + [current]
    lines, current = [], ""
    for ch in text:
        if current and font.size(current + ch)[0] > width and ch not in _NO_LINE_START:
            lines.append(current)
            current = ch
        else:
            current += ch
    return lines + [current] if current else lines


class Slider:
    """Horizontal 0..1 slider."""

    def __init__(self, rect: pygame.Rect, value: float, on_change: Callable[[float], None]) -> None:
        self.rect = rect
        self.value = value
        self.on_change = on_change
        self._dragging = False

    def _set_from_x(self, x: int) -> None:
        self.value = min(1.0, max(0.0, (x - self.rect.x) / self.rect.w))
        self.on_change(self.value)

    def handle(self, event: pygame.event.Event) -> bool:
        hit = self.rect.inflate(0, 16)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and hit.collidepoint(event.pos):
            self._dragging = True
            self._set_from_x(event.pos[0])
            return True
        if event.type == pygame.MOUSEMOTION and self._dragging:
            self._set_from_x(event.pos[0])
            return True
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self._dragging:
            self._dragging = False
            return True
        return False

    def nudge(self, delta: float) -> None:
        self.value = min(1.0, max(0.0, self.value + delta))
        self.on_change(self.value)

    def draw(self, surf: pygame.Surface) -> None:
        track = pygame.Rect(self.rect.x, self.rect.centery - 3, self.rect.w, 6)
        pygame.draw.rect(surf, theme.PANEL_EDGE, track, border_radius=3)
        filled = track.copy()
        filled.w = int(track.w * self.value)
        pygame.draw.rect(surf, theme.ACCENT, filled, border_radius=3)
        knob = (self.rect.x + int(self.rect.w * self.value), self.rect.centery)
        pygame.draw.circle(surf, (6, 6, 18), (knob[0], knob[1] + 2), 9)
        pygame.draw.circle(surf, theme.ACCENT, knob, 9)
        pygame.draw.circle(surf, theme.TEXT, knob, 9, width=2)


def draw_progress(surf: pygame.Surface, rect: pygame.Rect, fraction: float) -> None:
    pygame.draw.rect(surf, theme.PANEL_EDGE, rect, border_radius=4)
    inner = rect.inflate(-4, -4)
    inner.w = int(inner.w * min(1.0, max(0.0, fraction)))
    if inner.w > 0:
        pygame.draw.rect(surf, theme.GOOD, inner, border_radius=3)
