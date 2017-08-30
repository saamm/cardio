"""Contains Dirichlet model class."""

import dill
import numpy as np
import tensorflow as tf

from ..tf_base_model import TFBaseModel
from ..layers import conv_cell


class DirichletModel(TFBaseModel):  # pylint: disable=too-many-instance-attributes
    """Dirichlet model class.
    The model predicts Dirichlet distribution parameters from which classes probabilities are sampled.
    """

    def __init__(self):
        super().__init__()
        self._classes = None

        self._input_layer = None
        self._target = None
        self._is_training = None

        self._output_layer = None

        self._loss = None
        self._global_step = None
        self._train_step = None

    def build(self, input_shape, output_shape, classes):  # pylint: disable=protected-access
        """Build Dirichlet model.

        Parameters
        ----------
        input_shape : tuple
            The shape of signals to be fed.
        output_shape : tuple
            The shape of targets to be fed.
        classes : list
            List of classes names.

        Returns
        -------
        model : DirichletModel
            Built DirichletModel instance.
        """
        self._classes = classes
        input_shape = (None,) + input_shape
        output_shape = (None,) + output_shape
        k = 0.001

        self._graph = tf.Graph()
        with self.graph.as_default():  # pylint: disable=not-context-manager
            self._input_layer = tf.placeholder(tf.float32, shape=input_shape, name="input_layer")
            input_channels_last = tf.transpose(self._input_layer, perm=[0, 2, 1], name="channels_last")

            self._target = tf.placeholder(tf.float32, shape=output_shape, name="target")
            target = (1 - 2 * k) * self._target + k

            self._is_training = tf.placeholder(tf.bool, shape=[], name="is_training")

            n_filters = [10, 10, 10, 15, 15, 15, 20, 20, 20, 30, 30]
            kernel_size = [5, 5, 5, 5, 5, 5, 3, 3, 3, 3, 3]
            cell = input_channels_last
            for i, (n, s) in enumerate(zip(n_filters, kernel_size)):
                cell = conv_cell("cell_" + str(i + 1), cell, self._is_training, n, s)

            flat = tf.contrib.layers.flatten(cell)
            with tf.variable_scope("dense_1"):  # pylint: disable=not-context-manager
                dense = tf.layers.dense(flat, 8, use_bias=False, name="dense")
                bnorm = tf.layers.batch_normalization(dense, training=self._is_training, name="batch_norm")
                act = tf.nn.elu(bnorm, name="activation")

            with tf.variable_scope("dense_2"):  # pylint: disable=not-context-manager
                dense = tf.layers.dense(act, output_shape[1], use_bias=False, name="dense")
                bnorm = tf.layers.batch_normalization(dense, training=self._is_training, name="batch_norm")
                act = tf.nn.softplus(bnorm, name="activation")

            self._output_layer = tf.identity(act, name="output_layer")
            self._loss = tf.reduce_mean(tf.lbeta(self._output_layer) -
                                        tf.reduce_sum((self._output_layer - 1) * tf.log(target), axis=1),
                                        name="loss")

            update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
            with tf.control_dependencies(update_ops):
                self._global_step = tf.Variable(0, dtype=tf.int32, trainable=False, name="global_step")
                opt = tf.train.AdamOptimizer()
                self._train_step = opt.minimize(self._loss, global_step=self._global_step, name="train_step")

            self._session = tf.Session()
            self.session.run(tf.global_variables_initializer())
        return self

    def save(self, path):  # pylint: disable=arguments-differ
        """Save Dirichlet model.

        Parameters
        ----------
        path : str
            Path to the checkpoint file. MetaGraph is saved with "meta" suffix,
            classes names are saved with "dill" suffix.

        Returns
        -------
        model : DirichletModel
            DirichletModel instance unchanged.
        """
        with self.graph.as_default():  # pylint: disable=not-context-manager
            saver = tf.train.Saver()
            saver.save(self.session, path, global_step=self._global_step)
        classes_path = "{}-{}.dill".format(path, self.session.run(self._global_step))
        with open(classes_path, "wb") as file:
            dill.dump(self._classes, file)
        return self

    def load(self, graph_path, checkpoint_path, classes_path):  # pylint: disable=arguments-differ
        """Load Dirichlet model.

        Parameters
        ----------
        graph_path : str
            Path to the model MetaGraph.
        checkpoint_path : str
            Path to the model weights.
        classes_path : str
            Path to the model output classes names.

        Returns
        -------
        model : DirichletModel
            Loaded model.
        """
        self._graph = tf.Graph()
        with self.graph.as_default():  # pylint: disable=not-context-manager
            self._session = tf.Session()
            saver = tf.train.import_meta_graph(graph_path)
            saver.restore(self._session, checkpoint_path)
        tensor_names = ["input_layer", "target", "is_training", "output_layer", "loss", "global_step", "train_step"]
        for name in tensor_names:
            setattr(self, "_" + name, self.graph.get_tensor_by_name(name + ":0"))
        with open(classes_path, "rb") as file:
            self._classes = dill.load(file)
        return self

    @staticmethod
    def _concatenate_batch(batch):
        """Concatenate batch signals and targets.

        Parameters
        ----------
        batch : ModelEcgBatch
            Batch to concatenate.

        Returns
        -------
        x : 3-D ndarray
            Concatenated batch signals along axis 0.
        y : 2-D ndarray
            Concatenated batch targets along axis 0.
        split_indices : 1-D ndarray
            Splitting indices to revert signal concatenation.
        """
        x = np.concatenate(batch.signal)
        y = np.concatenate([np.tile(item.target, (item.signal.shape[0], 1)) for item in batch])
        split_indices = np.cumsum([item.signal.shape[0] for item in batch])[:-1]
        return x, y, split_indices

    @staticmethod
    def _append_result(batch, var_name, result):
        """Append result to the end of the pipeline variable var_name of type list.

        Parameters
        ----------
        batch : ModelEcgBatch
            Batch of signals.
        var_name : str or None
            Pipeline variable for results storing. If None, the variable is not created.
        result : misc
            Result to append.
        """
        if var_name is not None:
            batch.pipeline.get_variable(var_name, init=list, init_on_each_run=True).append(result)

    def train_on_batch(self, batch, loss_var_name=None):  # pylint: disable=arguments-differ
        """Run a single gradient update.

        Parameters
        ----------
        batch : ModelEcgBatch
            Batch of signals to train on.
        loss_var_name : str or None
            Pipeline variable for loss storing. If None, the variable is not created.

        Returns
        -------
        batch : ModelEcgBatch
            Input batch unchanged.
        """
        x, y, _ = self._concatenate_batch(batch)
        feed_dict = {self._input_layer: x, self._target: y, self._is_training: True}
        _, loss = self.session.run([self._train_step, self._loss], feed_dict=feed_dict)
        self._append_result(batch, loss_var_name, loss)
        return batch

    def test_on_batch(self, batch, loss_var_name):  # pylint: disable=arguments-differ
        """Get model loss for a single batch.

        Parameters
        ----------
        batch : ModelEcgBatch
            Batch of signals to calculate loss.
        loss_var_name : str
            Pipeline variable for loss storing.

        Returns
        -------
        batch : ModelEcgBatch
            Input batch unchanged.
        """
        x, y, _ = self._concatenate_batch(batch)
        feed_dict = {self._input_layer: x, self._target: y, self._is_training: False}
        loss = self.session.run(self._loss, feed_dict=feed_dict)
        self._append_result(batch, loss_var_name, loss)
        return batch

    @staticmethod
    def _get_dirichlet_mixture_stats(alpha):
        """Get mean and variance vectors of the mixture of Dirichlet distributions with equal weights
        and given parameters.

        Parameters
        ----------
        alpha : 2-D ndarray
            Dirichlet distribution parameters along axis 1 for each mixture component.

        Returns
        -------
        mean : 1-D ndarray
            Mean of the mixture.
        var : 1-D ndarray
            Variance of the mixture.
        """
        alpha_sum = np.sum(alpha, axis=1)[:, np.newaxis]
        comp_m1 = alpha / alpha_sum
        comp_m2 = (alpha * (alpha + 1)) / (alpha_sum * (alpha_sum + 1))
        mean = np.mean(comp_m1, axis=0)
        var = np.mean(comp_m2, axis=0) - mean**2
        return mean, var

    def predict_on_batch(self, batch, predictions_var_name):  # pylint: disable=arguments-differ
        """Get model predictions for a single batch.

        Parameters
        ----------
        batch : ModelEcgBatch
            Batch of signals to predict.
        predictions_var_name : str
            Pipeline variable for predictions storing.

        Returns
        -------
        batch : ModelEcgBatch
            Input batch unchanged.
        """
        n_classes = len(self._classes)
        max_var = (n_classes - 1) /  n_classes**2
        x, _, split_indices = self._concatenate_batch(batch)
        feed_dict = {self._input_layer: x, self._is_training: False}
        alpha = self.session.run(self._output_layer, feed_dict=feed_dict)
        alpha = np.split(alpha, split_indices)
        for a, t in zip(alpha, batch.target):
            mean, var = self._get_dirichlet_mixture_stats(a)
            uncertainty = var[np.argmax(mean)] / max_var
            predictions_dict = {"target_pred": dict(zip(self._classes, mean)),
                                "uncertainty": uncertainty}
            if t is not None:
                predictions_dict["target_true"] = dict(zip(self._classes, t))
            self._append_result(batch, predictions_var_name, predictions_dict)
        return batch
