"""ComfyUI nodes for the Higgsfield API: Seedance 2.5, Kling 4.0 and Genjutsu."""

from __future__ import annotations

import io
import uuid
from pathlib import Path

import numpy as np
from PIL import Image
from typing_extensions import override

import folder_paths
from comfy.model_management import processing_interrupted
from comfy_api.latest import IO, ComfyExtension, InputImpl, Types

from .higgsfield import models
from .higgsfield.client import HiggsfieldClient, HiggsfieldError, load_credentials, output_url

CATEGORY = "Higgsfield"
NODE_SUFFIX = "_Higgsfield"


def _report(node_id: str | None, text: str) -> None:
    print(f"[Higgsfield] {text}")
    if node_id is None:
        return
    try:
        from server import PromptServer

        PromptServer.instance.send_progress_text(text, node_id)
    except Exception:  # the UI note is a nicety; never fail a paid run over it
        pass


def _png_bytes(image) -> bytes:
    """First frame of a ComfyUI IMAGE tensor [B, H, W, C] as PNG."""
    array = np.clip(image[0].cpu().numpy() * 255.0, 0, 255).astype(np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="PNG")
    return buffer.getvalue()


def _mp4_bytes(video: Types.VideoInput) -> bytes:
    buffer = io.BytesIO()
    video.save_to(buffer, format=Types.VideoContainer.MP4, codec=Types.VideoCodec.H264)
    return buffer.getvalue()


def _client() -> HiggsfieldClient:
    return HiggsfieldClient(load_credentials())


def _upload_image(client: HiggsfieldClient, image, node_id) -> str:
    _report(node_id, "Uploading image…")
    return client.upload(_png_bytes(image), "image/png")


def _upload_frames(client: HiggsfieldClient, prompt: str, image, end_image, node_id) -> tuple[str | None, str | None]:
    """Check the frame inputs before anything is uploaded, then upload them."""
    if end_image is not None and image is None:
        raise ValueError("end_image needs a start image too.")
    if image is None and not prompt.strip():
        raise ValueError("Write a prompt, or connect a start image.")
    start = _upload_image(client, image, node_id) if image is not None else None
    end = _upload_image(client, end_image, node_id) if end_image is not None else None
    return start, end


def _generate(client: HiggsfieldClient, endpoint: str, body: dict, max_cost_usd: float, node_id) -> IO.NodeOutput:
    """Estimate, check the price against the cap, submit, wait and download."""
    cost = client.estimate(endpoint, body)
    if cost > max_cost_usd:
        raise HiggsfieldError(
            f"This run costs ${cost:.4f}, above max_cost_usd ${max_cost_usd:.2f}. "
            "Nothing was charged. Raise max_cost_usd to run it."
        )
    _report(node_id, f"Price ${cost:.4f}. Submitting {endpoint}…")
    request_id = client.submit(endpoint, body, idempotency_key=f"comfyui-{uuid.uuid4()}")

    def on_status(state: str, elapsed: float) -> None:
        _report(node_id, f"${cost:.4f} · {state} · {int(elapsed)} s\nrequest {request_id}")

    answer = client.wait(request_id, on_status=on_status, should_stop=processing_interrupted)
    url = output_url(answer)
    path = Path(folder_paths.get_temp_directory()) / f"higgsfield_{request_id}.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    client.download(url, path)
    _report(node_id, f"Done · ${cost:.4f}\nrequest {request_id}")
    return IO.NodeOutput(InputImpl.VideoFromFile(str(path)), url, cost)


def _common_inputs(default_cost: float) -> list:
    return [
        IO.Float.Input(
            "max_cost_usd",
            default=default_cost,
            min=0.0,
            max=100.0,
            step=0.05,
            tooltip="The node asks Higgsfield for the exact price first and stops, unpaid, "
            "if it is above this amount.",
        ),
        IO.Int.Input(
            "seed",
            default=0,
            min=0,
            max=2**32 - 1,
            control_after_generate=True,
            tooltip="Not sent to Higgsfield. Change it to run the same settings again.",
        ),
    ]


def _outputs() -> list:
    return [
        IO.Video.Output(display_name="video"),
        IO.String.Output(display_name="video_url", tooltip="Higgsfield keeps the file for 7 days."),
        IO.Float.Output(display_name="cost_usd", tooltip="The price Higgsfield estimated for this exact run."),
    ]


class Seedance25(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="Seedance25" + NODE_SUFFIX,
            display_name="Seedance 2.5 (Higgsfield)",
            category=CATEGORY,
            description="Text-to-video, or image-to-video when a start image is connected. "
            "Duration 4 to 30 seconds, with generated audio.",
            search_aliases=["seedance", "bytedance", "higgsfield video"],
            inputs=[
                IO.String.Input("prompt", multiline=True, default=""),
                IO.Int.Input("duration", default=5, min=4, max=30, display_mode=IO.NumberDisplay.slider),
                IO.Combo.Input("resolution", options=list(models.SEEDANCE_RATES), default="720p"),
                IO.Combo.Input(
                    "aspect_ratio",
                    options=["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
                    tooltip="Text-to-video only. With a start image the video keeps the image's frame.",
                ),
                IO.Boolean.Input("generate_audio", default=True),
                *_common_inputs(3.0),
                IO.Image.Input("image", optional=True, tooltip="First frame."),
                IO.Image.Input("end_image", optional=True, tooltip="Last frame. Needs a first frame."),
            ],
            outputs=_outputs(),
            hidden=[IO.Hidden.unique_id],
        )

    @classmethod
    def execute(
        cls, prompt, duration, resolution, aspect_ratio, generate_audio, max_cost_usd, seed, image=None, end_image=None
    ) -> IO.NodeOutput:
        node_id = cls.hidden.unique_id
        client = _client()
        image_url, end_image_url = _upload_frames(client, prompt, image, end_image, node_id)
        endpoint, body = models.seedance_25(
            prompt, duration, resolution, aspect_ratio, generate_audio, image_url, end_image_url
        )
        return _generate(client, endpoint, body, max_cost_usd, node_id)


class Kling40(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="Kling40" + NODE_SUFFIX,
            display_name="Kling 4.0 (Higgsfield)",
            category=CATEGORY,
            description="Text-to-video, or image-to-video when a start image is connected. "
            "Duration 3 to 30 seconds, 720p or 1080p.",
            search_aliases=["kling", "kling 4", "higgsfield video"],
            inputs=[
                IO.String.Input("prompt", multiline=True, default=""),
                IO.Int.Input("duration", default=5, min=3, max=30, display_mode=IO.NumberDisplay.slider),
                IO.Combo.Input("resolution", options=list(models.KLING4_RATES), default="1080p"),
                IO.Combo.Input(
                    "aspect_ratio",
                    options=["16:9", "9:16", "1:1", "21:9"],
                    tooltip="Text-to-video only. With a start image the video keeps the image's frame.",
                ),
                *_common_inputs(3.0),
                IO.Image.Input("image", optional=True, tooltip="First frame."),
                IO.Image.Input("end_image", optional=True, tooltip="Last frame. Needs a first frame."),
            ],
            outputs=_outputs(),
            hidden=[IO.Hidden.unique_id],
        )

    @classmethod
    def execute(
        cls, prompt, duration, resolution, aspect_ratio, max_cost_usd, seed, image=None, end_image=None
    ) -> IO.NodeOutput:
        node_id = cls.hidden.unique_id
        client = _client()
        image_url, end_image_url = _upload_frames(client, prompt, image, end_image, node_id)
        endpoint, body = models.kling_40(prompt, duration, resolution, aspect_ratio, image_url, end_image_url)
        return _generate(client, endpoint, body, max_cost_usd, node_id)


class GenjutsuMotionTransfer(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="GenjutsuMotionTransfer" + NODE_SUFFIX,
            display_name="Genjutsu Motion Transfer (Higgsfield)",
            category=CATEGORY,
            description="Puts the people or characters from the reference images into the source video's "
            "motion. The video must be at least 4 seconds. Genjutsu uses up to 30 seconds of it, "
            "Genjutsu Light up to 15 and keeps the source audio.",
            search_aliases=["genjutsu", "motion transfer", "character swap", "higgsfield video"],
            inputs=[
                IO.Video.Input("video", tooltip="Source video with the motion to copy."),
                IO.Image.Input("images", tooltip="1 to 8 reference images. A batch counts as several images."),
                IO.Combo.Input("model", options=list(models.GENJUTSU_ENDPOINTS), default="genjutsu-light"),
                IO.Combo.Input("resolution", options=["720p", "480p", "1080p"], default="720p"),
                IO.String.Input("prompt", multiline=True, default="", optional=True),
                *_common_inputs(5.0),
            ],
            outputs=_outputs(),
            hidden=[IO.Hidden.unique_id],
        )

    @classmethod
    def execute(cls, video, images, model, resolution, max_cost_usd, seed, prompt="") -> IO.NodeOutput:
        node_id = cls.hidden.unique_id
        if images.shape[0] > 8:
            raise ValueError(f"Genjutsu takes up to 8 reference images, got a batch of {images.shape[0]}.")
        seconds = video.get_duration()
        if seconds < 4:
            raise ValueError(f"The source video is {seconds:.1f} s long. Genjutsu needs at least 4 seconds.")
        client = _client()
        _report(node_id, "Uploading video…")
        video_url = client.upload(_mp4_bytes(video), "video/mp4")
        image_urls = [_upload_image(client, images[i : i + 1], node_id) for i in range(images.shape[0])]
        endpoint, body = models.genjutsu_motion_transfer(model, video_url, image_urls, prompt or "", resolution)
        return _generate(client, endpoint, body, max_cost_usd, node_id)


class HiggsfieldExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[IO.ComfyNode]]:
        return [Seedance25, Kling40, GenjutsuMotionTransfer]


async def comfy_entrypoint() -> HiggsfieldExtension:
    return HiggsfieldExtension()
