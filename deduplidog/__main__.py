import sys

from mininterface import Cancelled, run

from .deduplidog import Deduplidog


def main():
    with run(Deduplidog, interface=None) as m:
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
                except Cancelled:
                    continue
                except Exception as e:
                    print("-"*100)
                    print(e)
                    continue
                if not m.confirm("Continue?"):
                    break
        except KeyboardInterrupt:
            print("")
            sys.exit()
        # except Exception as e:
        #     import ipdb
        #     ipdb.post_mortem()


if __name__ == "__main__":
    main()
