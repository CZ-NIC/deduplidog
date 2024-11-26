import sys

from mininterface import run

from .deduplidog import Deduplidog


def main():
    with run(Deduplidog, interface=None) as m:
        # with run(Deduplidog, interface="tui") as m:
        # m = run(Deduplidog, interface="gui")
        # if 1:
        # m.facet._layout  # TODO
        try:
            while True:
                print("")
                m.form()
                m.facet._clear()
                try:
                    # if deduplidog:
                    #     # To prevent full inicialization with the slow metadata refresh, we re-use the same object.
                    #     [setattr(deduplidog, f.name, f.convert()) for f in dog_fields]
                    #     deduplidog.perform()
                    # else:
                    m.env.start(m)
                except Exception as e:
                    print("-"*100)
                    print(e)
                    continue
                if not m.is_yes("Continue?"):
                    break
        except KeyboardInterrupt:
            print("")
            sys.exit()
        except Exception as e:
            import ipdb
            ipdb.post_mortem()  # TODO


if __name__ == "__main__":
    main()
