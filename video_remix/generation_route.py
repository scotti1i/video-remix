"""显式生成路线；旧规格缺省仍使用多素材参考，不推断首帧。"""


ROUTES = {"reference": "multimodal2video", "first_frame": "image2video"}


def generation_route(value):
    route = value.get("generation_route", "reference")
    if not isinstance(route, str) or route not in ROUTES:
        raise ValueError("generation_route 必须是 reference / first_frame")
    return route


def validate_generation_route(value):
    route = generation_route(value)
    if route == "first_frame":
        inputs = value.get("inputs")
        if not isinstance(inputs, list) or len(inputs) != 1 or not isinstance(inputs[0], dict) \
                or inputs[0].get("type") != "image":
            raise ValueError("first_frame 只接受一张首帧图片，不接受额外图片或音视频")
        mode = value.get("mode", value.get("creative_mode", "image_text"))
        if mode != "image_text":
            raise ValueError("first_frame 仅支持 image_text，不改变输入模式含义")
    return route
