import argparse

from transformers import pipeline
from transformers.models.whisper.english_normalizer import BasicTextNormalizer
from datasets import load_dataset, concatenate_datasets, Audio
import evaluate

wer_metric = evaluate.load("wer")


def is_target_text_in_range(ref):
    if ref.strip() == "ignore time segment in scoring":
        return False
    else:
        return ref.strip() != ""


def get_text(sample):
    if "text" in sample:
        return sample["text"]
    elif "sentence" in sample:
        return sample["sentence"]
    elif "normalized_text" in sample:
        return sample["normalized_text"]
    elif "transcript" in sample:
        return sample["transcript"]
    elif "transcription" in sample:
        return sample["transcription"]
    else:
        raise ValueError(
            f"Expected transcript column of either 'text', 'sentence', 'normalized_text' or 'transcript'. Got sample of "
            ".join{sample.keys()}. Ensure a text column name is present in the dataset."
        )


whisper_norm = BasicTextNormalizer()


def normalise(batch):
    batch["norm_text"] = whisper_norm(get_text(batch))
    return batch


def data(dataset):
    for i, item in enumerate(dataset):
        yield {**item["audio"], "reference": item["norm_text"]}


def main(args):
    batch_size = args.batch_size
    whisper_asr = pipeline(
        "automatic-speech-recognition", model=args.model_id, device=args.device
    )
    for lang, config in zip(args.languages, args.configs):
        whisper_asr.model.config.forced_decoder_ids = (
            whisper_asr.tokenizer.get_decoder_prompt_ids(
                language=lang, task="transcribe"
            )
        )

        dataset = load_dataset(
            args.dataset,
            config,
            streaming=False
        )
        dataset = concatenate_datasets(list(dataset.values()))
        dataset = dataset.cast_column("audio", Audio(sampling_rate=16000))
        dataset = dataset.map(normalise)
        dataset = dataset.filter(is_target_text_in_range, input_columns=["norm_text"])

        predictions = []
        references = []

        # run streamed inference
        for out in whisper_asr(data(dataset), batch_size=batch_size):
            predictions.append(whisper_norm(out["text"]))
            references.append(out["reference"][0])

        wer = wer_metric.compute(references=references, predictions=predictions)

        print(f"{lang} WER: {wer*100}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--model_id",
        type=str,
        required=True,
        help="Model identifier. Should be loadable with 🤗 Transformers",
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default="google/fleurs",
        help="Dataset name to evaluate the `model_id`. Should be loadable with 🤗 Datasets",
    )
    parser.add_argument(
        "--configs",
        type=str,
        nargs='+',
        required=True,
        help="Configs of the dataset. *E.g.* `'en'` for the English split of Common Voice",
    )
    parser.add_argument(
        "--device",
        type=int,
        default=-1,
        help="The device to run the pipeline on. -1 for CPU (default), 0 for the first GPU and so on.",
    )
    parser.add_argument(
        "--batch_size",
        type=int,
        default=16,
        help="Number of samples to go through each streamed batch.",
    )
    parser.add_argument(
        "--max_eval_samples",
        type=int,
        default=None,
        help="Number of samples to be evaluated. Put a lower number e.g. 64 for testing this script.",
    )
    parser.add_argument(
        "--languages",
        type=str,
        nargs='+',
        required=True,
        help="Two letter language codes for the transcription languages, e.g. use 'en' for English.",
    )
    args = parser.parse_args()

    main(args)
