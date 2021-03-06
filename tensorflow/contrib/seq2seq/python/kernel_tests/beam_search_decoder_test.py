# Copyright 2017 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Tests for contrib.seq2seq.python.seq2seq.beam_search_decoder."""
# pylint: disable=unused-import,g-bad-import-order
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
# pylint: enable=unused-import

import numpy as np

from tensorflow.contrib.rnn import core_rnn_cell
from tensorflow.contrib.seq2seq.python.ops import attention_wrapper
from tensorflow.contrib.seq2seq.python.ops import beam_search_decoder
from tensorflow.contrib.seq2seq.python.ops import decoder
from tensorflow.python.framework import constant_op
from tensorflow.python.framework import dtypes
from tensorflow.python.framework import ops
from tensorflow.python.layers import core as layers_core
from tensorflow.python.ops import array_ops
from tensorflow.python.ops import nn_ops
from tensorflow.python.ops import variables
from tensorflow.python.platform import test

# pylint: enable=g-import-not-at-top


class TestGatherTree(test.TestCase):
  """Tests the gather_tree function."""

  def test_gather_tree(self):
    predicted_ids = np.array([[[1, 2, 3], [4, 5, 6], [7, 8, 9]],
                              [[2, 3, 4], [5, 6, 7],
                               [8, 9, 10]]]).transpose([1, 0, 2])
    parent_ids = np.array([
        [[0, 0, 0], [0, 1, 1], [2, 1, 2]],
        [[0, 0, 0], [1, 2, 0], [2, 1, 1]],
    ]).transpose([1, 0, 2])
    expected_result = np.array([[[2, 2, 2], [6, 5, 6], [7, 8, 9]],
                                [[2, 4, 4], [7, 6, 6],
                                 [8, 9, 10]]]).transpose([1, 0, 2])

    res = beam_search_decoder._gather_tree(
        ops.convert_to_tensor(predicted_ids), ops.convert_to_tensor(parent_ids))

    with self.test_session() as sess:
      res_ = sess.run(res)

    np.testing.assert_array_equal(expected_result, res_)


class TestEosMasking(test.TestCase):
  """Tests EOS masking used in beam search."""

  def test_eos_masking(self):
    probs = constant_op.constant([
        [[-.2, -.2, -.2, -.2, -.2], [-.3, -.3, -.3, 3, 0], [5, 6, 0, 0, 0]],
        [[-.2, -.2, -.2, -.2, 0], [-.3, -.3, -.1, 3, 0], [5, 6, 3, 0, 0]],
    ])

    eos_token = 0
    previously_finished = constant_op.constant(
        [[0, 1, 0], [0, 1, 1]], dtype=dtypes.float32)
    masked = beam_search_decoder._mask_probs(probs, eos_token,
                                             previously_finished)

    with self.test_session() as sess:
      probs = sess.run(probs)
      masked = sess.run(masked)

      np.testing.assert_array_equal(probs[0][0], masked[0][0])
      np.testing.assert_array_equal(probs[0][2], masked[0][2])
      np.testing.assert_array_equal(probs[1][0], masked[1][0])

      np.testing.assert_equal(masked[0][1][0], 0)
      np.testing.assert_equal(masked[1][1][0], 0)
      np.testing.assert_equal(masked[1][2][0], 0)

      for i in range(1, 5):
        np.testing.assert_approx_equal(masked[0][1][i], np.finfo('float32').min)
        np.testing.assert_approx_equal(masked[1][1][i], np.finfo('float32').min)
        np.testing.assert_approx_equal(masked[1][2][i], np.finfo('float32').min)


class TestBeamStep(test.TestCase):
  """Tests a single step of beam search."""

  def setUp(self):
    super(TestBeamStep, self).setUp()
    self.batch_size = 2
    self.beam_width = 3
    self.vocab_size = 5
    self.end_token = 0
    self.length_penalty_weight = 0.6

  def test_step(self):
    dummy_cell_state = array_ops.zeros([self.batch_size, self.beam_width])
    beam_state = beam_search_decoder.BeamSearchDecoderState(
        cell_state=dummy_cell_state,
        log_probs=nn_ops.log_softmax(
            array_ops.ones([self.batch_size, self.beam_width])),
        lengths=constant_op.constant(
            2, shape=[self.batch_size, self.beam_width], dtype=dtypes.int32),
        finished=array_ops.zeros(
            [self.batch_size, self.beam_width], dtype=dtypes.bool))

    logits_ = np.full([self.batch_size, self.beam_width, self.vocab_size],
                      0.0001)
    logits_[0, 0, 2] = 1.9
    logits_[0, 0, 3] = 2.1
    logits_[0, 1, 3] = 3.1
    logits_[0, 1, 4] = 0.9
    logits_[1, 0, 1] = 0.5
    logits_[1, 1, 2] = 2.7
    logits_[1, 2, 2] = 10.0
    logits_[1, 2, 3] = 0.2
    logits = ops.convert_to_tensor(logits_, dtype=dtypes.float32)
    log_probs = nn_ops.log_softmax(logits)

    outputs, next_beam_state = beam_search_decoder._beam_search_step(
        time=2,
        logits=logits,
        beam_state=beam_state,
        batch_size=ops.convert_to_tensor(self.batch_size),
        beam_width=self.beam_width,
        end_token=self.end_token,
        length_penalty_weight=self.length_penalty_weight)

    with self.test_session() as sess:
      outputs_, next_state_, state_, log_probs_ = sess.run(
          [outputs, next_beam_state, beam_state, log_probs])

    np.testing.assert_array_equal(outputs_.predicted_ids, [[3, 3, 2], [2, 2,
                                                                       1]])
    np.testing.assert_array_equal(outputs_.parent_ids, [[1, 0, 0], [2, 1, 0]])
    np.testing.assert_array_equal(next_state_.lengths, [[3, 3, 3], [3, 3, 3]])
    np.testing.assert_array_equal(next_state_.finished, [[False, False, False],
                                                         [False, False, False]])

    expected_log_probs = []
    expected_log_probs.append(state_.log_probs[0][[1, 0, 0]])
    expected_log_probs.append(state_.log_probs[1][[2, 1, 0]])  # 0 --> 1
    expected_log_probs[0][0] += log_probs_[0, 1, 3]
    expected_log_probs[0][1] += log_probs_[0, 0, 3]
    expected_log_probs[0][2] += log_probs_[0, 0, 2]
    expected_log_probs[1][0] += log_probs_[1, 2, 2]
    expected_log_probs[1][1] += log_probs_[1, 1, 2]
    expected_log_probs[1][2] += log_probs_[1, 0, 1]
    np.testing.assert_array_equal(next_state_.log_probs, expected_log_probs)

  def test_step_with_eos(self):
    dummy_cell_state = array_ops.zeros([self.batch_size, self.beam_width])
    beam_state = beam_search_decoder.BeamSearchDecoderState(
        cell_state=dummy_cell_state,
        log_probs=nn_ops.log_softmax(
            array_ops.ones([self.batch_size, self.beam_width])),
        lengths=ops.convert_to_tensor(
            [[2, 1, 2], [2, 2, 1]], dtype=dtypes.int32),
        finished=ops.convert_to_tensor(
            [[False, True, False], [False, False, True]], dtype=dtypes.bool))

    logits_ = np.full([self.batch_size, self.beam_width, self.vocab_size],
                      0.0001)
    logits_[0, 0, 2] = 1.9
    logits_[0, 0, 3] = 2.1
    logits_[0, 1, 3] = 3.1
    logits_[0, 1, 4] = 0.9
    logits_[1, 0, 1] = 0.5
    logits_[1, 1, 2] = 5.7  # why does this not work when it's 2.7?
    logits_[1, 2, 2] = 1.0
    logits_[1, 2, 3] = 0.2
    logits = ops.convert_to_tensor(logits_, dtype=dtypes.float32)
    log_probs = nn_ops.log_softmax(logits)

    outputs, next_beam_state = beam_search_decoder._beam_search_step(
        time=2,
        logits=logits,
        beam_state=beam_state,
        batch_size=ops.convert_to_tensor(self.batch_size),
        beam_width=self.beam_width,
        end_token=self.end_token,
        length_penalty_weight=self.length_penalty_weight)

    with self.test_session() as sess:
      outputs_, next_state_, state_, log_probs_ = sess.run(
          [outputs, next_beam_state, beam_state, log_probs])

    np.testing.assert_array_equal(outputs_.parent_ids, [[1, 0, 0], [1, 2, 0]])
    np.testing.assert_array_equal(outputs_.predicted_ids, [[0, 3, 2], [2, 0,
                                                                       1]])
    np.testing.assert_array_equal(next_state_.lengths, [[1, 3, 3], [3, 1, 3]])
    np.testing.assert_array_equal(next_state_.finished, [[True, False, False],
                                                         [False, True, False]])

    expected_log_probs = []
    expected_log_probs.append(state_.log_probs[0][[1, 0, 0]])
    expected_log_probs.append(state_.log_probs[1][[1, 2, 0]])
    expected_log_probs[0][1] += log_probs_[0, 0, 3]
    expected_log_probs[0][2] += log_probs_[0, 0, 2]
    expected_log_probs[1][0] += log_probs_[1, 1, 2]
    expected_log_probs[1][2] += log_probs_[1, 0, 1]
    np.testing.assert_array_equal(next_state_.log_probs, expected_log_probs)


class BeamSearchDecoderTest(test.TestCase):

  def _testDynamicDecodeRNN(self, time_major, has_attention):
    encoder_sequence_length = [3, 2, 3, 1, 0]
    decoder_sequence_length = [2, 0, 1, 2, 3]
    batch_size = 5
    decoder_max_time = 4
    input_depth = 7
    cell_depth = 9
    attention_depth = 6
    vocab_size = 20
    end_token = vocab_size - 1
    start_token = 0
    embedding_dim = 50
    max_out = max(decoder_sequence_length)
    output_layer = layers_core.Dense(vocab_size, use_bias=True, activation=None)
    beam_width = 3

    with self.test_session() as sess:
      embedding = np.random.randn(vocab_size, embedding_dim).astype(np.float32)
      cell = core_rnn_cell.LSTMCell(cell_depth)
      if has_attention:
        inputs = np.random.randn(batch_size, decoder_max_time,
                                 input_depth).astype(np.float32)
        attention_mechanism = attention_wrapper.BahdanauAttention(
            num_units=attention_depth,
            memory=inputs,
            memory_sequence_length=encoder_sequence_length)
        cell = attention_wrapper.AttentionWrapper(
            cell=cell,
            attention_mechanism=attention_mechanism,
            attention_size=attention_depth,
            alignment_history=False)
      cell_state = cell.zero_state(
          dtype=dtypes.float32, batch_size=batch_size * beam_width)
      bsd = beam_search_decoder.BeamSearchDecoder(
          cell=cell,
          embedding=embedding,
          start_tokens=batch_size * [start_token],
          end_token=end_token,
          initial_state=cell_state,
          beam_width=beam_width,
          output_layer=output_layer,
          length_penalty_weight=0.0)

      final_outputs, final_state = decoder.dynamic_decode(
          bsd, output_time_major=time_major, maximum_iterations=max_out)

      def _t(shape):
        if time_major:
          return (shape[1], shape[0]) + shape[2:]
        return shape

      self.assertTrue(
          isinstance(final_outputs,
                     beam_search_decoder.FinalBeamSearchDecoderOutput))
      self.assertTrue(
          isinstance(final_state, beam_search_decoder.BeamSearchDecoderState))

      beam_search_decoder_output = final_outputs.beam_search_decoder_output
      self.assertEqual(
          _t((batch_size, None, beam_width)),
          tuple(beam_search_decoder_output.scores.get_shape().as_list()))
      self.assertEqual(
          _t((batch_size, None, beam_width)),
          tuple(final_outputs.predicted_ids.get_shape().as_list()))

      sess.run(variables.global_variables_initializer())
      sess_results = sess.run({
          'final_outputs': final_outputs,
          'final_state': final_state
      })

      # Mostly a smoke test
      time_steps = max_out
      self.assertEqual(
          _t((batch_size, time_steps, beam_width)),
          sess_results['final_outputs'].beam_search_decoder_output.scores.shape)
      self.assertEqual(
          _t((batch_size, time_steps, beam_width)), sess_results[
              'final_outputs'].beam_search_decoder_output.predicted_ids.shape)

  def testDynamicDecodeRNNBatchMajorNoAttention(self):
    self._testDynamicDecodeRNN(time_major=False, has_attention=False)

  def testDynamicDecodeRNNBatchMajorYesAttention(self):
    self._testDynamicDecodeRNN(time_major=False, has_attention=True)


if __name__ == '__main__':
  test.main()
