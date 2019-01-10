"""Data loading and processing"""

import os
import kgekit.io
import kgekit.utils
import kgedata
import torch
from torch.utils.data import Dataset
import numpy as np
import random
from torchvision import transforms
import functools
from kgexpr.data import collators, constants, transformers


class TripleSource(object):
    """Triple stores."""

    TRAIN_FILENAME = "train.txt"
    VALID_FILENAME = "valid.txt"
    TEST_FILENAME = "test.txt"

    def __init__(self, data_dir, triple_order, delimiter):
        """loads the data.
        Args:
            Directory with {train,valid,test}.txt
        """
        self.data_dir = data_dir
        self._train_set, num_failed = kgekit.io.read_triple_indexes(
            os.path.join(self.data_dir, self.TRAIN_FILENAME),
            triple_order=triple_order,
            delimiter=delimiter)
        assert num_failed == 0
        self._valid_set, num_failed = kgekit.io.read_triple_indexes(
            os.path.join(self.data_dir, self.VALID_FILENAME),
            triple_order=triple_order,
            delimiter=delimiter)
        assert num_failed == 0
        self._test_set, num_failed = kgekit.io.read_triple_indexes(
            os.path.join(self.data_dir, self.TEST_FILENAME),
            triple_order=triple_order,
            delimiter=delimiter)
        assert num_failed == 0
        head_compare = lambda x: x.head
        tail_compare = lambda x: x.tail
        relation_compare = lambda x: x.relation
        max_head = max([
            max(triple_set, key=head_compare) for triple_set in
            [self._train_set, self._valid_set, self._test_set]
        ],
                       key=head_compare)
        max_tail = max([
            max(triple_set, key=tail_compare) for triple_set in
            [self._train_set, self._valid_set, self._test_set]
        ],
                       key=tail_compare)
        self._num_entity = max(max_head.head, max_tail.tail) + 1
        self._num_relation = max([
            max(triple_set, key=relation_compare) for triple_set in
            [self._train_set, self._valid_set, self._test_set]
        ],
                                 key=relation_compare).relation + 1

    @property
    def train_set(self):
        return self._train_set

    @property
    def valid_set(self):
        return self._valid_set

    @property
    def test_set(self):
        return self._test_set

    @property
    def num_entity(self):
        return self._num_entity

    @property
    def num_relation(self):
        return self._num_relation


class TripleIndexesDataset(Dataset):
    """Loads triple indexes dataset."""

    def __init__(self,
                 triple_source,
                 dataset_type=constants.DatasetType.TRAINING,
                 transform=None):
        """
        Args:
            triple_source: Triple storage.
            dataset_type: Choose the type of dataset.
            transform (callable, optional): Optional transform to be applied
                on a sample.
        """
        if dataset_type == constants.DatasetType.TRAINING:
            self.triples = triple_source.train_set
        elif dataset_type == constants.DatasetType.VALIDATION:
            self.triples = triple_source.valid_set
        elif dataset_type == constants.DatasetType.TESTING:
            self.triples = triple_source.test_set
        else:
            raise RuntimeError("DatasetType doesn't exists. It's " +
                               str(dataset_type))
        self.transform = transform
        if self.transform:
            self.triples = self.transform(self.triples)

    def __len__(self):
        return len(self.triples)

    def __getitem__(self, idx):
        sample = self.triples[idx]

        return sample



def get_triples_from_batch(batch):
    """Returns h, r, t and possible label from batch."""

    batch_size, num_samples, num_element = batch.shape
    elements = np.split(batch, num_element, axis=2)
    if num_samples <= 1:
        return (e.reshape(batch_size) for e in elements)
    else:
        return (e.reshape(batch_size, num_samples) for e in elements)


def np_to_tensor(x, cuda_enabled=False):
    x = torch.from_numpy(x)
    # Input is an index to find relevant embeddings. We don't track them
    x.requires_grad_(False)
    if cuda_enabled:
        x = x.cuda()
    return x

class _BatchElementConverter(object):
    def __init__(self, cuda_enabled=False):
        self.cuda_enabled = cuda_enabled

    def __call__(self, x):
        return np_to_tensor(x, self.cuda_enabled)


def convert_triple_tuple_to_torch(batch, config, enable_cuda_override=None):
    if enable_cuda_override is not None:
        converter = _BatchElementConverter(enable_cuda_override)
    else:
        converter = _BatchElementConverter(config.enable_cuda)
    return tuple(map(converter, batch))


def expand_triple_to_sets(triple, num_expands, arange_target):
    """Tiles triple into a large sets for testing. One node will be initialized with arange.
    Returns (h, r, t), each with a shape of (num_expands,)
    """

    if not constants.TripleElement.has_value(arange_target):
        raise RuntimeError(
            "arange_target is set to wrong value. It has to be one of TripleElement but it's "
            + str(arange_target))
    h, r, t = triple

    if arange_target == constants.TripleElement.HEAD:
        h = np.arange(num_expands, dtype=np.int64)
        r = np.tile(np.array([r], dtype=np.int64), num_expands)
        t = np.tile(np.array([t], dtype=np.int64), num_expands)
    elif arange_target == constants.TripleElement.RELATION:
        h = np.tile(np.array([h], dtype=np.int64), num_expands)
        r = np.arange(num_expands, dtype=np.int64)
        t = np.tile(np.array([t], dtype=np.int64), num_expands)
    elif arange_target == constants.TripleElement.TAIL:
        h = np.tile(np.array([h], dtype=np.int64), num_expands)
        r = np.tile(np.array([r], dtype=np.int64), num_expands)
        t = np.arange(num_expands, dtype=np.int64)
    else:
        raise RuntimeError(
            "Miracle happened. arange_target passed the validation and reached impossible branch."
        )

    return (h, r, t)


_SAFE_MINIMAL_BATCH_SIZE = 1


class TripleIndexBatchSampler(object):
    """Samples a mini-batch of triple indexes."""

    def __init__(self, dataset, batch_size):
        self.dataset = dataset
        self.batch_size = batch_size

    def __iter__(self):
        # sequence iterator
        self.cursor = 0
        self.max_item = len(self.dataset)
        return self

    def __len__(self):
        # if self.drop_last:
        #     return len(self.sampler) // self.batch_size
        return (len(self.dataset) + self.batch_size - 1) // self.batch_size

    def __next__(self):
        if self.cursor + self.batch_size < self.max_item:
            batch = np.empty((self.batch_size), dtype=np.int64)
            for i in range(self.batch_size):
                batch[i] = self.cursor + i
            self.cursor += self.batch_size
        else: # last batch
            batch_size = self.max_item - self.cursor
            if batch_size <= 0:
                raise StopIteration()
            batch = np.empty((batch_size), dtype=np.int64)
            for i in range(batch_size):
                batch[i] = self.cursor + i
            self.cursor += batch_size
        return batch

SEED_OFFSET = 100

def create_dataloader(triple_source,
                      config,
                      collates_label=False,
                      dataset_type=constants.DatasetType.TRAINING):
    """Creates dataloader with certain types"""
    dataset = TripleIndexesDataset(
        triple_source,
        dataset_type,
        transform=transformers.OrderedTripleListTransform(config.triple_order))

    # Use those C++ extension is fast but then we can't use spawn method to start data loader.
    if dataset_type == constants.DatasetType.TRAINING:
        negative_sampler = kgedata.LCWANoThrowSampler(
            triple_source.train_set,
            triple_source.num_entity,
            triple_source.num_relation,
            config.negative_entity,
            config.negative_relation,
            config.base_seed,
            kgedata.LCWANoThrowSamplerStrategy.Hash)
        corruptor = kgedata.BernoulliCorruptor(triple_source.train_set, triple_source.num_relation, config.base_seed+SEED_OFFSET)

        collates = [
            collators.list_stack_collate,
            collators.CorruptionCollate(corruptor),
            collators.LCWANoThrowCollate(
                triple_source,
                negative_sampler),
        ]
        if collates_label:
            collates.append(collators.label_collate)
        else:
            collates.append(collators.none_label_collate)
        batch_size = config.batch_size
        collates.append(collators.BreakdownCollator(config))
    else:  # Validation and Test
        batch_size = max(_SAFE_MINIMAL_BATCH_SIZE,
                         int(config.batch_size * config.evaluation_load_factor))
        collates = [collators.TripleTileCollate(config, triple_source)]
        if collates_label:
            collates.append(collators.label_prediction_collate)
    collate_fn = transforms.Compose(collates)


    batch_sampler = TripleIndexBatchSampler(dataset, batch_size)
    data_loader = torch.utils.data.DataLoader(
        dataset,
        batch_sampler=batch_sampler,
        num_workers=config.num_workers,
        pin_memory=True,  # May cause system froze because of not enough physical memory
        collate_fn=collate_fn,
    )
    return data_loader


def sieve_and_expand_triple(triple_source, entities, relations, head, relation,
                            tail):
    """Tile on a unknown element. returns a tuple of size 3 with h, r, t."""

    batch_size, num_samples, num_element = batch.shape
    elements = np.split(batch, num_element, axis=2)
    # return (e.reshape(batch_size) for e in elements)

    if head == '?':
        r = relations[relation]
        t = entities[tail]
        triple_index = kgedata.TripleIndex(-1, r, t)

        h = np.arange(triple_source.num_entity, dtype=np.int64)
        r = np.tile(np.array([r], dtype=np.int64), triple_source.num_entity)
        t = np.tile(np.array([t], dtype=np.int64), triple_source.num_entity)
        prediction_type = constants.HEAD_KEY
    elif relation == '?':
        h = entities[head]
        t = entities[tail]
        triple_index = kgedata.TripleIndex(h, -1, t)

        h = np.tile(np.array([h], dtype=np.int64), triple_source.num_relation)
        r = np.arange(triple_source.num_relation, dtype=np.int64)
        t = np.tile(np.array([t], dtype=np.int64), triple_source.num_relation)
        prediction_type = constants.RELATION_KEY
    elif tail == '?':
        r = relations[relation]
        h = entities[head]
        triple_index = kgedata.TripleIndex(h, r, -1)

        h = np.tile(np.array([h], dtype=np.int64), triple_source.num_entity)
        r = np.tile(np.array([r], dtype=np.int64), triple_source.num_entity)
        t = np.arange(triple_source.num_entity, dtype=np.int64)
        prediction_type = constants.TAIL_KEY
    else:
        raise RuntimeError("head, relation, tail are known.")

    return (h, r, t), prediction_type, triple_index


# class LiteralCollate(object):
#     def __init__(self):
#             self.source,
#             negative_sampler,
#             literals=['facts'],
#             sample_negative_for_non_triples=False,
#             transforms=dict(
#                 triple_transform=data.OrderedTripleListTransform("hrt"),
#                 fact_transform=data.FactTransform(),
#             ),
#         )(self.samples, 0)
