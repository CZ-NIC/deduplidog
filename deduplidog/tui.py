from dataclasses import dataclass, field

from textual import events
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Checkbox, Footer, Input, Label


@dataclass
class TuiState:
    INPUTS: list = field(default_factory=list)
    FOCUSED_I: int = 0


tui_state = TuiState()


class CheckboxApp(App[None]):
    CSS_PATH = "form.tcss"

    BINDINGS = [
        ("up", "go_up", "Go up"),
        ("down", "go_up", "Go down"),
        ("ctrl+s", "confirm", "Run"),  # ctrl/alt+enter does not work; enter does not work with checkboxes
        ("escape", "exit", "Exit"),
    ]

    def compose(self) -> ComposeResult:
        yield Footer()
        self.inputs = tui_state.INPUTS
        with VerticalScroll():
            for input in self.inputs:
                if isinstance(input, Input):
                    yield Label(input.placeholder)
                yield input
                yield Label(input._link.help)
                yield Label("")

    def on_mount(self):
        self.inputs[tui_state.FOCUSED_I].focus()

    def action_confirm(self):
        # next time, start on the same widget
        tui_state.FOCUSED_I = next((i for i, inp in enumerate(self.inputs) if inp == self.focused), None)
        self.exit(True)

    def action_exit(self):
        self.exit()

    def on_key(self, event: events.Key) -> None:
        try:
            index = self.inputs.index(self.focused)
        except ValueError:  # probably some other element were focused
            return
        match event.key:
            case "down":
                self.inputs[(index + 1) % len(self.inputs)].focus()
            case "up":
                self.inputs[(index - 1) % len(self.inputs)].focus()
            case letter if len(letter) == 1:  # navigate by letters
                for inp_ in self.inputs[index+1:] + self.inputs[:index]:
                    label = inp_.label if isinstance(inp_, Checkbox) else inp_.placeholder
                    if str(label).casefold().startswith(letter):
                        inp_.focus()
                        break
