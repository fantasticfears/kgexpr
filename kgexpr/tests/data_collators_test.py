import unittest
from kgexpr import data
from kgexpr.data import collators
import kgekit
from torchvision import transforms
import numpy as np
import torch
import pytest


class Config(object):
    """Mocked implementation of config"""
    data_dir = "kgexpr/tests/fixtures/triples"
    triple_order = "hrt"
    triple_delimiter = ' '
    negative_entity = 1
    negative_relation = 1
    batch_size = 100
    num_workers = 1
    entity_embedding_dimension = 10
    margin = 0.01
    epoches = 1


@pytest.mark.numpyfile
class DataTest(unittest.TestCase):
    @classmethod
    def setUp(cls):
        cls.triple_dir = 'kgexpr/tests/fixtures/triples'
        cls.source = data.TripleSource(cls.triple_dir, 'hrt', ' ')
        cls.dataset = data.TripleIndexesDataset(cls.source)
        cls.small_triple_list = [
            kgekit.TripleIndex(0, 0, 1),
            kgekit.TripleIndex(1, 1, 2)
        ]
        cls.samples = ([True, False], cls.small_triple_list)

    def test_uniform_collate(self):
        np.random.seed(0)
        self.assertEqual(
            collators.UniformCorruptionCollate()(self.small_triple_list),
            ([False, False], self.small_triple_list))

    def test_bernoulli_corruption_collate(self):
        np.random.seed(0)
        corruptor = kgekit.BernoulliCorruptor(self.source.train_set)
        self.assertEqual(
            collators.BernoulliCorruptionCollate(self.source, corruptor)(
                self.small_triple_list),
            ([False, False], self.small_triple_list))

    def test_lcwa_no_throw_collate(self):
        np.random.seed(0)
        negative_sampler = kgekit.LCWANoThrowSampler(
            self.source.train_set, self.source.num_entity,
            self.source.num_relation, 1, 1,
            kgekit.LCWANoThrowSamplerStrategy.Hash)
        batch, negatives = collators.LCWANoThrowCollate(
            self.source,
            negative_sampler,
            transform=data.OrderedTripleListTransform("hrt"))(self.samples, 0)
        np.testing.assert_equal(
            batch, np.array([
                [[0, 0, 1]],
                [[1, 1, 2]],
            ], dtype=np.int64))
        np.testing.assert_equal(
            negatives,
            np.array([
                [[0, 0, 3], [0, 2, 1]],
                [[0, 1, 2], [1, 0, 2]],
            ],
                     dtype=np.int64))