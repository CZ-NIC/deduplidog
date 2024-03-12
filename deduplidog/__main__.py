import sys
from dataclasses import fields
from typing import get_args

import click
from dataclass_click import dataclass_click
from textual import events
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Checkbox, Footer, Input, Label

from .interface_utils import Field
from .deduplidog import Deduplidog


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
        self.inputs = INPUTS
        with VerticalScroll():
            for input in self.inputs:
                if isinstance(input, Input):
                    yield Label(input.placeholder)
                yield input
                yield Label(input._link.help)
                yield Label("")

    def on_mount(self):
        self.inputs[0].focus()

    def action_confirm(self):
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


class RaiseOnMissingParam(click.Command):
    def __call__(self, *args, **kwargs):
        return super(RaiseOnMissingParam, self).__call__(*args, standalone_mode=False, **kwargs)


@click.command(cls=RaiseOnMissingParam)
@dataclass_click(Deduplidog)
def cli(dd: Deduplidog):
    return dd


def main():
    global INPUTS

    # CLI
    try:
        dd = cli()
        if not dd:  # maybe just --help
            return
        if input("See more options? [Y/n] ").casefold() not in ("", "y"):
            sys.exit()
    except click.MissingParameter:
        # User launched the program without parameters.
        # This is not a problem, we have TUI instead.
        dd = None

    # TUI
    dog_fields: list[Field] = []
    for f in fields(Deduplidog):
        try:
            dog_fields.append(Field(f.name,
                                    getattr(dd, f.name, f.default),
                                    get_args(f.type)[0],
                                    get_args(f.type)[1].kwargs["help"]))
        except Exception as e:
            # we want only documented fields, in case of an incorrenctly defined field, we do not let user to edit
            continue
    while True:
        print("")
        INPUTS = [f.get_widgets() for f in dog_fields]
        if not CheckboxApp().run():
            break
        for form, field in zip(INPUTS, dog_fields):
            field.value = form.value
        try:
            Deduplidog(**{f.name: f.convert() for f in dog_fields})
        except Exception as e:
            print("-"*100)
            print(e)
            input()
            continue
        if input("See more options? [Y/n] ").casefold() not in ("y", ""):
            break

if __name__ == "__main__":
    main()