# Copyright 2023 The KerasNLP Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""BERT token packing layer."""

import tensorflow as tf
from tensorflow import keras

from keras_nlp.api_export import keras_nlp_export
from keras_nlp.utils.tf_utils import assert_tf_text_installed

try:
    import tensorflow_text as tf_text
except ImportError:
    tf_text = None


@keras_nlp_export("keras_nlp.layers.MultiSegmentPacker")
class MultiSegmentPacker(keras.layers.Layer):
    """Packs multiple sequences into a single fixed width model input.

    This layer packs multiple input sequences into a single fixed width sequence
    containing start and end delimeters, forming an dense input suitable for a
    classification task for BERT and BERT-like models.

    Takes as input a list or tuple of token segments. The layer will process
    inputs as follows:
     - Truncate all input segments to fit within `sequence_length` according to
       the `truncate` strategy.
     - Concatenate all input segments, adding a single `start_value` at the
       start of the entire sequence, and multiple `end_value`s at the end of
       each segment.
     - Pad the resulting sequence to `sequence_length` using `pad_tokens`.
     - Calculate a separate tensor of "segment ids", with integer type and the
       same shape as the packed token output, where each integer index of the
       segment the token originated from. The segment id of the `start_value`
       is always 0, and the segment id of each `end_value` is the segment that
       precedes it.

    Input should be either a `tf.RaggedTensor` or a dense `tf.Tensor`, and
    either rank-1 or rank-2.

    Args:
        sequence_length: int. The desired output length.
        start_value: int/str/list/tuple. The id(s) or token(s) that are to be
            placed at the start of each sequence (called "[CLS]" for BERT). The
            dtype must match the dtype of the input tensors to the layer.
        end_value: int/str/list/tuple. The id(s) or token(s) that are to be
            placed at the end of the last input segment (called "[SEP]" for
            BERT). The dtype must match the dtype of the input tensors to the
            layer.
        sep_value: int/str/list/tuple. The id(s) or token(s) that are to be
            placed at the end of every segment, except the last segment (called
            "[SEP]" for BERT). If `None`, `end_value` is used. The dtype must
            match the dtype of the input tensors to the layer.
        pad_value: int/str. The id or token that is to be placed into the unused
            positions after the last segment in the sequence
            (called "[PAD]" for BERT).
        truncate: str. The algorithm to truncate a list of batched segments to
            fit a per-example length limit. The value can be either
            `"round_robin"` or `"waterfall"`:
                - `"round_robin"`: Available space is assigned one token at a
                    time in a round-robin fashion to the inputs that still need
                    some, until the limit is reached.
                - `"waterfall"`: The allocation of the budget is done using a
                    "waterfall" algorithm that allocates quota in a
                    left-to-right manner and fills up the buckets until we run
                    out of budget. It support arbitrary number of segments.

    Returns:
        A tuple with two elements. The first is the dense, packed token
        sequence. The second is an integer tensor of the same shape, containing
        the segment ids.

    Examples:

    *Pack a single input for classification.*
    >>> seq1 = tf.constant([1, 2, 3, 4])
    >>> packer = keras_nlp.layers.MultiSegmentPacker(
    ...     8, start_value=101, end_value=102)
    >>> packer(seq1)
    (<tf.Tensor: shape=(8,), dtype=int32,
        numpy=array([101, 1, 2, 3, 4, 102, 0, 0], dtype=int32)>,
     <tf.Tensor: shape=(8,), dtype=int32,
        numpy=array([0, 0, 0, 0, 0, 0, 0, 0], dtype=int32)>)

    *Pack multiple inputs for classification.*
    >>> seq1 = tf.constant([1, 2, 3, 4])
    >>> seq2 = tf.constant([11, 12, 13, 14])
    >>> packer = keras_nlp.layers.MultiSegmentPacker(
    ...     8, start_value=101, end_value=102)
    >>> packer((seq1, seq2))
    (<tf.Tensor: shape=(8,), dtype=int32,
        numpy=array([101,   1,   2,   3, 102,  11,  12, 102], dtype=int32)>,
     <tf.Tensor: shape=(8,), dtype=int32,
        numpy=array([0, 0, 0, 0, 0, 1, 1, 1], dtype=int32)>)

    *Pack multiple inputs for classification with different sep tokens.*
    >>> seq1 = tf.constant([1, 2, 3, 4])
    >>> seq2 = tf.constant([11, 12, 13, 14])
    >>> packer = keras_nlp.layers.MultiSegmentPacker(
    ...     8, start_value=101, end_value=102, sep_value=[102, 102])
    >>> packer((seq1, seq2))
    (<tf.Tensor: shape=(8,), dtype=int32,
        numpy=array([101,   1,   2, 102, 102,  11,  12, 102], dtype=int32)>,
    <tf.Tensor: shape=(8,), dtype=int32,
        numpy=array([0, 0, 0, 0, 0, 1, 1, 1], dtype=int32)>)

    Reference:
        [Devlin et al., 2018](https://arxiv.org/abs/1810.04805).
    """

    def __init__(
        self,
        sequence_length,
        start_value,
        end_value,
        sep_value=None,
        pad_value=None,
        truncate="round_robin",
        **kwargs,
    ):
        assert_tf_text_installed(self.__class__.__name__)

        super().__init__(**kwargs)
        self.sequence_length = sequence_length
        if truncate not in ("round_robin", "waterfall"):
            raise ValueError(
                "Only 'round_robin' and 'waterfall' algorithms are "
                "supported. Received %s" % truncate
            )
        self.truncate = truncate

        # Maintain private copies of start/end values for config purposes.
        self._start_value = start_value
        self._sep_value = sep_value
        self._end_value = end_value

        def check_special_value_type(value, value_name):
            if isinstance(value, (int, str)):
                return [value]
            if value and not isinstance(value, (list, tuple)):
                raise ValueError(
                    f"{value_name} should be of type int/str/list/tuple."
                    f"Received type: `{type(value)}`."
                )
            return value

        start_value = check_special_value_type(start_value, "start_value")
        if sep_value is None:
            sep_value = end_value
        sep_value = check_special_value_type(sep_value, "sep_value")
        end_value = check_special_value_type(end_value, "end_value")

        self.start_value = start_value
        self.sep_value = sep_value
        self.end_value = end_value

        self.pad_value = pad_value

    def get_config(self):
        config = super().get_config()
        config.update(
            {
                "sequence_length": self.sequence_length,
                "start_value": self._start_value,
                "end_value": self._end_value,
                "sep_value": self._sep_value,
                "pad_value": self.pad_value,
                "truncate": self.truncate,
            }
        )
        return config

    def _sanitize_inputs(self, inputs):
        """Force inputs to a list of rank 2 ragged tensors."""
        # Sanitize inputs.
        if not isinstance(inputs, (list, tuple)):
            inputs = [inputs]
        if not inputs:
            raise ValueError("At least one input is required for packing")
        input_ranks = [x.shape.rank for x in inputs]
        if not all(0 < rank < 3 for rank in input_ranks):
            raise ValueError(
                "All inputs for packing must have rank 1 or 2. "
                f"Received input ranks: {input_ranks}"
            )
        if None in input_ranks or len(set(input_ranks)) > 1:
            raise ValueError(
                "All inputs for packing must have the same rank. "
                f"Received input ranks: {input_ranks}"
            )
        return inputs

    def _convert_dense(self, x):
        """Converts inputs to rank 2 ragged tensors."""
        if isinstance(x, tf.Tensor):
            return tf.RaggedTensor.from_tensor(x)
        else:
            return x

    def _trim_inputs(self, inputs):
        """Trim inputs to desired length."""
        num_segments = len(inputs)
        num_special_tokens = (
            len(self.start_value)
            + (num_segments - 1) * len(self.sep_value)
            + len(self.end_value)
        )
        if self.truncate == "round_robin":
            return tf_text.RoundRobinTrimmer(
                self.sequence_length - num_special_tokens
            ).trim(inputs)
        elif self.truncate == "waterfall":
            return tf_text.WaterfallTrimmer(
                self.sequence_length - num_special_tokens
            ).trim(inputs)
        else:
            raise ValueError("Unsupported truncate: %s" % self.truncate)

    def _combine_inputs(self, segments):
        """Combine inputs with start and end values added."""
        dtype = segments[0].dtype
        batch_size = segments[0].nrows()
        start_value = tf.convert_to_tensor(self.start_value, dtype=dtype)
        sep_value = tf.convert_to_tensor(self.sep_value, dtype=dtype)
        end_value = tf.convert_to_tensor(self.end_value, dtype=dtype)

        start_columns = tf.repeat(
            start_value[tf.newaxis, :], repeats=batch_size, axis=0
        )
        sep_columns = tf.repeat(
            sep_value[tf.newaxis, :], repeats=batch_size, axis=0
        )
        end_columns = tf.repeat(
            end_value[tf.newaxis, :], repeats=batch_size, axis=0
        )
        ones_sep_columns = tf.ones_like(sep_columns, dtype=tf.int32)
        ones_end_columns = tf.ones_like(end_columns, dtype=tf.int32)

        segments_to_combine = [start_columns]
        segment_ids_to_combine = [
            tf.ones_like(start_columns, dtype=tf.int32) * 0
        ]

        for i, seg in enumerate(segments):
            # Combine all segments.
            segments_to_combine.append(seg)

            # Combine segment ids.
            segment_ids_to_combine.append(tf.ones_like(seg, dtype=tf.int32) * i)

            # Account for the sep/end tokens here.
            if i == len(segments) - 1:
                segments_to_combine.append(end_columns)
                segment_ids_to_combine.append(ones_end_columns * i)
            else:
                segments_to_combine.append(sep_columns)
                segment_ids_to_combine.append(ones_sep_columns * i)

        token_ids = tf.concat(segments_to_combine, 1)
        segment_ids = tf.concat(segment_ids_to_combine, 1)
        return token_ids, segment_ids

    def call(self, inputs):
        inputs = self._sanitize_inputs(inputs)

        # If rank 1, add a batch dim.
        rank_1 = inputs[0].shape.rank == 1
        if rank_1:
            inputs = [tf.expand_dims(x, 0) for x in inputs]
        inputs = [self._convert_dense(x) for x in inputs]

        segments = self._trim_inputs(inputs)
        token_ids, segment_ids = self._combine_inputs(segments)
        # Pad to dense tensor output.
        shape = tf.cast([-1, self.sequence_length], tf.int64)
        token_ids = token_ids.to_tensor(
            shape=shape, default_value=self.pad_value
        )
        segment_ids = segment_ids.to_tensor(shape=shape)
        # Remove the batch dim if added.
        if rank_1:
            token_ids = tf.squeeze(token_ids, 0)
            segment_ids = tf.squeeze(segment_ids, 0)

        return (token_ids, segment_ids)
