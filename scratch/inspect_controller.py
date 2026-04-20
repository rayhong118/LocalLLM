from browser_use import Controller
controller = Controller()

print(f"Controller attributes: {dir(controller)}")
if hasattr(controller, 'registry'):
    print(f"Registry attributes: {dir(controller.registry)}")
    # Try common names
    for attr in ['actions', '_actions', 'get_actions', 'list_actions']:
        if hasattr(controller.registry, attr):
            print(f"Found attribute: {attr}")
            res = getattr(controller.registry, attr)
            if callable(res):
                print(f"Result of {attr}(): {res()}")
            else:
                print(f"Value of {attr}: {res}")
