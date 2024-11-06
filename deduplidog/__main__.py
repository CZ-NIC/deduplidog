import sys
from dataclasses import fields
from typing import get_args

from click import MissingParameter

from .tui import CheckboxApp, tui_state
from .cli import cli

from .helpers import Field
from .deduplidog import Deduplidog


def main():
    try:
        # CLI
        try:
            deduplidog = cli()
            if not deduplidog:  # maybe just --help
                return
            if input("See more options? [Y/n] ").casefold() not in ("", "y"):
                sys.exit()
        except MissingParameter:
            # User launched the program without parameters.
            # This is not a problem, we have TUI instead.
            deduplidog = None

        # TUI
        dog_fields: list[Field] = []
        for f in fields(Deduplidog):
            try:
                dog_fields.append(Field(f.name,
                                        getattr(deduplidog, f.name, f.default),
                                        get_args(f.type)[0],
                                        get_args(f.type)[1].kwargs["help"]))
            except Exception as e:
                # we want only documented fields, in case of an incorrenctly defined field, we do not let user to edit
                continue
        tui_state.FOCUSED_I = 0
        while True:
            print("")
            tui_state.INPUTS = [f.get_widgets() for f in dog_fields]
            if not CheckboxApp().run():
                break
            for form, field in zip(tui_state.INPUTS, dog_fields):
                field.value = form.value
            try:
                # if deduplidog:
                #     # To prevent full inicialization with the slow metadata refresh, we re-use the same object.
                #     [setattr(deduplidog, f.name, f.convert()) for f in dog_fields]
                #     deduplidog.perform()
                # else:
                deduplidog = Deduplidog(**{f.name: f.convert() for f in dog_fields})
            except Exception as e:
                print("-"*100)
                print(e)
                input()
                continue
            if input("See more options? [Y/n] ").casefold() not in ("y", ""):
                break
    except KeyboardInterrupt:
        print("")
        sys.exit()


if __name__ == "__main__":
    main()
