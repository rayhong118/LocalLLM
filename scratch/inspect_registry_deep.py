from browser_use import Controller
controller = Controller()

if hasattr(controller, 'registry'):
    reg = controller.registry
    print(f"Registry.registry type: {type(reg.registry)}")
    print(f"Registry.registry contents: {dir(reg.registry)}")
    if hasattr(reg, 'registry') and isinstance(reg.registry, dict):
        print(f"Registry.registry keys: {reg.registry.keys()}")
