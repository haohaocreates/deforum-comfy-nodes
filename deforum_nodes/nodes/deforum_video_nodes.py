import base64
import os
from io import BytesIO

import cv2
import imageio
import numpy as np
import torch
from tqdm import tqdm

import folder_paths
from ..modules.deforum_comfyui_helpers import tensor2pil, pil2tensor, find_next_index, pil_image_to_base64, tensor_to_webp_base64

video_extensions = ['webm', 'mp4', 'mkv', 'gif']

import moviepy.editor as mp
from scipy.io.wavfile import write
import tempfile


def save_to_file(data, filepath: str):
    # Ensure the audio data is reshaped properly for mono/stereo
    if data.num_channels > 1:
        audio_data_reshaped = data.audio_data.reshape((-1, data.num_channels))
    else:
        audio_data_reshaped = data.audio_data
    write(filepath, data.sample_rate, audio_data_reshaped.astype(np.int16))
    return True

class DeforumLoadVideo:

    def __init__(self):
        self.video_path = None

    @classmethod
    def INPUT_TYPES(s):
        input_dir = folder_paths.get_input_directory()
        files = []
        for f in os.listdir(input_dir):
            if os.path.isfile(os.path.join(input_dir, f)):
                file_parts = f.split('.')
                if len(file_parts) > 1 and (file_parts[-1] in video_extensions):
                    files.append(f)
        return {"required": {
                    "video": (sorted(files),),
                    "reset": ("BOOLEAN", {"default": False},),

        },}

    CATEGORY = "deforum/video"
    display_name = "Load Video"

    RETURN_TYPES = ("IMAGE","INT","INT")
    RETURN_NAMES = ("IMAGE","FRAME_IDX","MAX_FRAMES")
    FUNCTION = "load_video_frame"

    def __init__(self):
        self.cap = None
        self.current_frame = None

    def load_video_frame(self, video, reset):
        video_path = folder_paths.get_annotated_filepath(video)

        # Initialize or reset video capture
        if self.cap is None or self.cap.get(cv2.CAP_PROP_POS_FRAMES) >= self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or self.video_path != video_path or reset:
            try:
                self.cap.release()
            except:
                pass
            self.cap = cv2.VideoCapture(video_path)

            self.cap = cv2.VideoCapture(video_path)
            self.current_frame = -1
            self.video_path = video_path



        success, frame = self.cap.read()
        if success:
            self.current_frame += 1
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame = np.array(frame).astype(np.float32)
            frame = pil2tensor(frame)  # Convert to torch tensor
        else:
            # Reset if reached the end of the video
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            success, frame = self.cap.read()
            self.current_frame = 0
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame = np.array(frame).astype(np.float32)
            frame = pil2tensor(frame)  # Convert to torch tensor

        return (frame,self.current_frame,self.cap.get(cv2.CAP_PROP_POS_FRAMES),)

    @classmethod
    def IS_CHANGED(cls, text, autorefresh):
        # Force re-evaluation of the node
        if autorefresh == "Yes":
            return float("NaN")

    @classmethod
    def VALIDATE_INPUTS(cls, video):
        if not folder_paths.exists_annotated_filepath(video):
            return "Invalid video file: {}".format(video)
        return True

class DeforumVideoSaveNode:
    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()
        self.images = []
        self.size = None
    @classmethod
    def INPUT_TYPES(s):
        return {"required":
                    {"image": ("IMAGE",),
                     "filename_prefix": ("STRING",{"default":"Deforum"}),
                     "fps": ("INT", {"default": 24, "min": 1, "max": 10000},),
                     "codec": (["libx265", "libx264", "libvpx-vp9", "libaom-av1", "mpeg4", "libvpx"],),
                     "pixel_format": (["yuv420p", "yuv422p", "yuv444p", "yuvj420p", "yuvj422p", "yuvj444p", "rgb24", "rgba", "nv12", "nv21"],),
                     "format": (["mp4", "mov", "gif", "avi"],),
                     "quality": ("INT", {"default": 10, "min": 1, "max": 10},),
                     "dump_by": (["max_frames", "per_N_frames"],),
                     "dump_every": ("INT", {"default": 0, "min": 0, "max": 4096},),
                     "dump_now": ("BOOLEAN", {"default": False},),
                     "skip_save": ("BOOLEAN", {"default": False},),
                     "skip_return": ("BOOLEAN", {"default": True},),
                     "enable_preview": ("BOOLEAN", {"default": True},),
                     "restore": ("BOOLEAN", {"default": False},),
                     },
                "optional": {
                    "deforum_frame_data": ("DEFORUM_FRAME_DATA",),
                    "audio": ("AUDIO",),
                    "waveform_image": ("IMAGE",),
                },
                "hidden": {
                    "js_frames": ("INT", {"default": 0, "min": 0, "max": 9999999999},),
                }

                }

    RETURN_TYPES = ("IMAGE",)
    OUTPUT_NODE = True

    FUNCTION = "fn"
    display_name = "Save Video"
    CATEGORY = "deforum/video"
    def add_image(self, image):
        self.images.append(image)

    def fn(self,
           image,
           filename_prefix,
           fps,
           codec,
           pixel_format,
           format,
           quality,
           dump_by,
           dump_every,
           dump_now,
           skip_save,
           skip_return,
           enable_preview,
           deforum_frame_data={},
           audio=None,
           waveform_image=None,
           restore=False):

        dump = False
        ret = "skip"
        full_output_folder, filename, counter, subfolder, filename_prefix = folder_paths.get_save_image_path(
            filename_prefix, self.output_dir)
        counter = find_next_index(full_output_folder, filename_prefix, format)
        anim_args = deforum_frame_data.get("anim_args")

        if image is not None:

            if anim_args is not None:
                max_frames = anim_args.max_frames
            else:
                max_frames = image.shape[0] + len(self.images) + 2
            if not deforum_frame_data.get("reset", None):
                if image.shape[0] > 1:
                    for img in image:
                        self.add_image(img)
                else:
                    self.add_image(image[0])
            print(f"[deforum] Video Save node cached {len(self.images)} frames")
            print("THE CAT IS FINE. SOMETHING WAS False THOUGH THE CAT IS NOT")

            # When the current frame index reaches the last frame, save the video

            if dump_by == "max_frames":
                dump = len(self.images) >= max_frames
            else:
                dump = len(self.images) >= dump_every
            if deforum_frame_data.get("reset", None):
                dump = True
            ret = "skip"
            if dump or dump_now:  # frame_idx is 0-based
                if len(self.images) >= 2:
                    if not skip_save:
                        self.save_video(full_output_folder, filename, counter, fps, audio, codec, format)
                    if not skip_return:
                        ret = torch.stack([pil2tensor(i)[0] for i in self.images], dim=0)
                self.images = []  # Empty the list for next use

            if deforum_frame_data.get("reset", None):
                if image.shape[0] > 1:
                    for img in image:
                        self.add_image(img)
                else:
                    self.add_image(image[0])
        if enable_preview and image is not None:
            # if audio is not None:
            base64_audio = self.encode_audio_base64(audio, len(self.images), fps)
            # else:
            #     base64_audio = None
            ui_ret = {"counter":(len(self.images),),
                      "should_dump":(dump or dump_now,),
                      "frames":([tensor_to_webp_base64(i) for i in image] if not restore else [tensor_to_webp_base64(i) for i in self.images]),
                      "fps":(fps,),
                      "audio":(base64_audio,)}
            if waveform_image is not None:
                ui_ret["waveform"] = (tensor_to_webp_base64(waveform_image),)
        else:
            if anim_args is not None:
                max_frames = anim_args.max_frames
            else:
                max_frames = len(self.images) + 5
            if dump_by == "max_frames":
                dump = len(self.images) >= max_frames
            else:
                dump = len(self.images) >= dump_every
            if deforum_frame_data.get("reset", None):
                dump = True
                dump_now = True
            if dump or dump_now:  # frame_idx is 0-based
                if len(self.images) >= 2:
                    if not skip_save:
                        self.save_video(full_output_folder, filename, counter, fps, audio, codec, format)
                    if not skip_return:
                        ret = torch.stack([pil2tensor(i)[0] for i in self.images], dim=0)
                self.images = []
            ui_ret = {"counter":(len(self.images),),
                      "should_dump":(dump or dump_now,),
                      "frames":([]),
                      "fps":(fps,)}

        return {"ui": ui_ret, "result": (ret,)}

    def encode_audio_base64(self, audio_data, frame_count, fps):
        sample_rate = 44100  # Default sample rate

        if audio_data is None:
            # Generate silent audio data
            duration_in_seconds = frame_count / float(fps)
            silence = np.zeros(int(duration_in_seconds * sample_rate), dtype=np.int16)
            audio_data_reshaped = silence
        else:
            # Handle actual audio data
            num_samples_to_keep = int((frame_count / fps) * audio_data.sample_rate)
            if audio_data.num_channels > 1:
                audio_data_reshaped = audio_data.audio_data.reshape((-1, audio_data.num_channels))
            else:
                audio_data_reshaped = audio_data.audio_data
            actual_samples = audio_data_reshaped.shape[0]
            if actual_samples > num_samples_to_keep:
                audio_data_reshaped = audio_data_reshaped[:num_samples_to_keep, ...]
            elif actual_samples < num_samples_to_keep:
                padding_length = num_samples_to_keep - actual_samples
                if audio_data.num_channels > 1:
                    padding = np.zeros((padding_length, audio_data.num_channels), dtype=audio_data_reshaped.dtype)
                else:
                    padding = np.zeros(padding_length, dtype=audio_data_reshaped.dtype)
                audio_data_reshaped = np.vstack((audio_data_reshaped, padding))

        # Convert the numpy array to bytes and encode in base64
        output = BytesIO()
        write(output, sample_rate, audio_data_reshaped)
        base64_audio = base64.b64encode(output.getvalue()).decode('utf-8')
        return base64_audio

    def save_video(self, full_output_folder, filename, counter, fps, audio, codec, ext):
        output_path = os.path.join(full_output_folder, f"{filename}_{counter}.{ext}")

        print("[deforum] Saving video:", output_path)

        # writer = imageio.get_writer(output_path, fps=fps, codec=codec, quality=quality, pixelformat=pixel_format, format=format)
        # for frame in tqdm(self.images, desc=f"Saving {format} (imageio)"):
        #     writer.append_data(np.clip(255. * frame.detach().cpu().numpy().squeeze(), 0, 255).astype(np.uint8))
        # writer.close()
        video_clip = mp.ImageSequenceClip(
            [np.clip(255. * frame.detach().cpu().numpy().squeeze(), 0, 255).astype(np.uint8) for frame in
             self.images], fps=fps)
        if audio is not None:
            # Generate a temporary file for the audio
            with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as tmp_audio_file:
                save_to_file(audio, tmp_audio_file.name)
                # Load the audio clip
                audio_clip = mp.AudioFileClip(tmp_audio_file.name)
                # Calculate video duration
                video_duration = len(self.images) / fps
                # Trim or loop the audio clip to match the video length
                if audio_clip.duration > video_duration:
                    audio_clip = audio_clip.subclip(0,
                                                    video_duration)  # Trim the audio to match video length
                elif audio_clip.duration < video_duration:
                    # If you want to loop the audio, uncomment the following line
                    # audio_clip = audio_clip.loop(duration=video_duration)
                    pass  # If you prefer silence after the audio ends, do nothing
                # Set the audio on the video clip
                video_clip = video_clip.set_audio(audio_clip)

        video_clip.write_videofile(output_path, codec=codec, audio_codec='aac')

    @classmethod
    def IS_CHANGED(s, text, autorefresh):
        # Force re-evaluation of the node
        if autorefresh == "Yes":
            return float("NaN")


def encode_audio_base64(audio_data, frame_count, fps):
    # Calculate the target duration of the audio in seconds based on the video duration
    target_audio_duration = frame_count / fps

    # Calculate the number of samples to keep based on the target duration and the sample rate
    num_samples_to_keep = int(target_audio_duration * audio_data.sample_rate)

    # Reshape the audio data based on the number of channels
    if audio_data.num_channels > 1:
        audio_data_reshaped = audio_data.audio_data.reshape((-1, audio_data.num_channels))
    else:
        audio_data_reshaped = audio_data.audio_data

    # Trim or pad the audio data to match the target duration
    actual_samples = audio_data_reshaped.shape[0]
    if actual_samples > num_samples_to_keep:
        # Trim the audio data if it's longer than the target duration
        audio_data_reshaped = audio_data_reshaped[:num_samples_to_keep, ...]
    elif actual_samples < num_samples_to_keep:
        # Pad the audio data with zeros if it's shorter than the target duration
        padding_length = num_samples_to_keep - actual_samples
        if audio_data.num_channels > 1:
            padding = np.zeros((padding_length, audio_data.num_channels), dtype=audio_data_reshaped.dtype)
        else:
            padding = np.zeros(padding_length, dtype=audio_data_reshaped.dtype)
        audio_data_reshaped = np.vstack((audio_data_reshaped, padding))

    # Convert the adjusted numpy array to bytes
    output = BytesIO()
    write(output, audio_data.sample_rate, audio_data_reshaped.astype(np.int16))

    # Encode bytes to base64
    base64_audio = base64.b64encode(output.getvalue()).decode('utf-8')

    return base64_audio
def save_to_file(data, filepath: str):
    # Ensure the audio data is reshaped properly for mono/stereo
    if data.num_channels > 1:
        audio_data_reshaped = data.audio_data.reshape((-1, data.num_channels))
    else:
        audio_data_reshaped = data.audio_data
    write(filepath, data.sample_rate, audio_data_reshaped.astype(np.int16))
    return True