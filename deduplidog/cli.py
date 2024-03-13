import click
from dataclass_click import dataclass_click

from .deduplidog import Deduplidog


class RaiseOnMissingParam(click.Command):
    def __call__(self, *args, **kwargs):
        return super(RaiseOnMissingParam, self).__call__(*args, standalone_mode=False, **kwargs)


@click.command(cls=RaiseOnMissingParam)
@dataclass_click(Deduplidog)
def cli(dd: Deduplidog):
    return dd
