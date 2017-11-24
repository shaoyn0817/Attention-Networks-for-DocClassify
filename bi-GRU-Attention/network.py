# -*- coding:utf-8 -*-
import sys
sys.path.insert(0, '../../')
import tensorflow as tf
from tensorflow.contrib import rnn
import tensorflow.contrib.layers as layers
import config as cfg

"""wd_1_1_cnn_concat
title 部分使用 TextCNN；content 部分使用 TextCNN； 两部分输出直接 concat。
"""
preFolder='../../'

class Settings(object):
    def __init__(self):
        self.model_name = 'biGRU'
        self.title_len = cfg.PAD_TITLE_LEEHTH
        self.content_len = cfg.PAD_CONTENT_LEENTH
        self.hidden_size = 256
        self.n_layer=1
        self.fc_hidden_size = 1024
        self.n_class = 2
        self.summary_path = preFolder+cfg.file_summary_path + self.model_name + '/'
        self.ckpt_path =preFolder+cfg.file_ckpt_path + self.model_name + '/'


class GRU_Atten(object):
    """
    title: inputs->textcnn->output_title
    content: inputs->textcnn->output_content
    concat[output_title, output_content] -> fc+bn+relu -> sigmoid_entropy.
    """

    def __init__(self, W_embedding, settings):
        self.model_name = settings.model_name
        self.title_len = settings.title_len
        self.content_len = settings.content_len
        self.n_layer = settings.n_layer
        self.n_class = settings.n_class
        self.hidden_size =settings.hidden_size 
        self.fc_hidden_size = settings.fc_hidden_size
        self._global_step = tf.Variable(0, trainable=False, name='Global_Step')
        self.update_emas = list()
        # placeholders
        self._tst = tf.placeholder(tf.bool)
        self._keep_prob = tf.placeholder(tf.float32, [])
        self._batch_size = tf.placeholder(tf.int32, [])

        with tf.name_scope('Inputs'):
            self._X1_inputs = tf.placeholder(tf.int64, [None, self.title_len], name='X1_inputs')
            self._X2_inputs = tf.placeholder(tf.int64, [None, self.content_len], name='X2_inputs')
            self._y_inputs = tf.placeholder(tf.float32, [None, self.n_class], name='y_input')

        with tf.variable_scope('embedding'):
            self.embedding = tf.get_variable(name='embedding', shape=W_embedding.shape,
                                             initializer=tf.constant_initializer(W_embedding), trainable=True)
        self.embedding_size = W_embedding.shape[1]

        with tf.variable_scope('bigru_text'):
            output_title = self.bigru_inference(self._X1_inputs)

        with tf.variable_scope('bigru_content'):
            output_content = self.bigru_inference(self._X2_inputs)

       
        with tf.variable_scope('fc-bn-layer'):
            output = tf.concat([output_title, output_content], axis=1)
            output = tf.nn.dropout(output, keep_prob=self._keep_prob)
            W_fc = self.weight_variable([self.hidden_size*4, self.fc_hidden_size], name='Weight_fc')
            tf.summary.histogram('W_fc', W_fc)
            h_fc = tf.matmul(output, W_fc, name='h_fc')
            beta_fc = tf.Variable(tf.constant(0.1, tf.float32, shape=[self.fc_hidden_size], name="beta_fc"))
            tf.summary.histogram('beta_fc', beta_fc)
            fc_bn, update_ema_fc = self.batchnorm(h_fc, beta_fc, convolutional=False)
            self.update_emas.append(update_ema_fc)
            temp = tf.nn.relu(fc_bn, name="relu")
            self.fc_bn_relu = tf.nn.dropout(x=temp, keep_prob=self._keep_prob)

        with tf.variable_scope('out_layer'):
            W_out = self.weight_variable([self.fc_hidden_size, self.n_class], name='Weight_out')
            tf.summary.histogram('Weight_out', W_out)
            b_out = self.bias_variable([self.n_class], name='bias_out')
            tf.summary.histogram('bias_out', b_out)
            self._y_pred = tf.nn.xw_plus_b(self.fc_bn_relu, W_out, b_out, name='y_pred')  # 每个类别的分数 scores

        with tf.name_scope('loss'):
            self._loss = tf.reduce_mean(
                tf.nn.sigmoid_cross_entropy_with_logits(logits=self._y_pred, labels=self._y_inputs))
            tf.summary.scalar('loss', self._loss)

        self.saver = tf.train.Saver(max_to_keep=2)

    @property
    def tst(self):
        return self._tst

    @property
    def keep_prob(self):
        return self._keep_prob

    @property
    def batch_size(self):
        return self._batch_size

    @property
    def global_step(self):
        return self._global_step

    @property
    def X1_inputs(self):
        return self._X1_inputs

    @property
    def X2_inputs(self):
        return self._X2_inputs

    @property
    def y_inputs(self):
        return self._y_inputs

    @property
    def y_pred(self):
        return self._y_pred

    @property
    def loss(self):
        return self._loss

    def weight_variable(self, shape, name):
        """Create a weight variable with appropriate initialization."""
        #initial = tf.truncated_normal(shape, stddev=0.1)
        weights = tf.get_variable(name=name, shape=shape, initializer=tf.contrib.layers.xavier_initializer(dtype=tf.float32))
        return weights

    def bias_variable(self, shape, name):
        """Create a bias variable with appropriate initialization."""
        #initial = tf.constant(0.1, shape=shape)
        bias = tf.get_variable(name=name, shape=shape, initializer=tf.constant_initializer(value=0.0, dtype=tf.float32))
        return bias

    def batchnorm(self, Ylogits, offset, convolutional=False):
        """batchnormalization.
        Args:
            Ylogits: 1D向量或者是3D的卷积结果。
            num_updates: 迭代的global_step
            offset：表示beta，全局均值；在 RELU 激活中一般初始化为 0.1。
            scale：表示lambda，全局方差；在 sigmoid 激活中需要，这 RELU 激活中作用不大。
            m: 表示batch均值；v:表示batch方差。
            bnepsilon：一个很小的浮点数，防止除以 0.
        Returns:
            Ybn: 和 Ylogits 的维度一样，就是经过 Batch Normalization 处理的结果。
            update_moving_everages：更新mean和variance，主要是给最后的 test 使用。
        """
        exp_moving_avg = tf.train.ExponentialMovingAverage(0.999,
                                                           self._global_step)  # adding the iteration prevents from averaging across non-existing iterations
        bnepsilon = 1e-5
        if convolutional:
            mean, variance = tf.nn.moments(Ylogits, [0, 1, 2])
        else:
            mean, variance = tf.nn.moments(Ylogits, [0])
        update_moving_everages = exp_moving_avg.apply([mean, variance])
        m = tf.cond(self.tst, lambda: exp_moving_avg.average(mean), lambda: mean)
        v = tf.cond(self.tst, lambda: exp_moving_avg.average(variance), lambda: variance)
        Ybn = tf.nn.batch_normalization(Ylogits, m, v, offset, None, bnepsilon)
        return Ybn, update_moving_everages
    
    def gru_cell(self, inputs):
        with tf.name_scope('gru_cell'):
            cell = rnn.GRUCell(self.hidden_size, reuse=tf.get_variable_scope().reuse)
        return rnn.DropoutWrapper(cell, output_keep_prob=self.keep_prob, variational_recurrent=True, input_size=tf.shape(inputs)[1], dtype=tf.float32)
 
    def gru_cell_no_dropout(self):
        with tf.name_scope('gru_cell'):
            cell = rnn.GRUCell(self.hidden_size, reuse=tf.get_variable_scope().reuse)
        return cell
    
    def bi_gru(self, inputs):
        """build the bi-GRU network. 返回个所有层的隐含状态。"""
        cells_fw = [self.gru_cell_no_dropout() for _ in range(self.n_layer)]
        cells_bw = [self.gru_cell_no_dropout() for _ in range(self.n_layer)]
        initial_states_fw = [cell_fw.zero_state(self.batch_size, tf.float32) for cell_fw in cells_fw]
        initial_states_bw = [cell_bw.zero_state(self.batch_size, tf.float32) for cell_bw in cells_bw]
        outputs, _, _ = rnn.stack_bidirectional_dynamic_rnn(cells_fw, cells_bw, inputs,
                                                            initial_states_fw=initial_states_fw,
                                                            initial_states_bw=initial_states_bw, dtype=tf.float32)
        return outputs
    
    def task_specific_attention(self, inputs, output_size,
                                initializer=layers.xavier_initializer(),
                                activation_fn=tf.tanh, scope=None):
        """
        Performs task-specific attention reduction, using learned
        attention context vector (constant within task of interest).
        Args:
            inputs: Tensor of shape [batch_size, units, input_size]
                `input_size` must be static (known)
                `units` axis will be attended over (reduced from output)
                `batch_size` will be preserved
            output_size: Size of output's inner (feature) dimension
        Returns:
           outputs: Tensor of shape [batch_size, output_dim].
        """
        assert len(inputs.get_shape()) == 3 and inputs.get_shape()[-1].value is not None
        with tf.variable_scope(scope or 'attention') as scope:
            # u_w, attention 向量
            atten_w1 = tf.get_variable(name='atten_w1', shape=[output_size],
                                                       initializer=initializer, dtype=tf.float32)
            atten_b1 = tf.get_variable(name='atten_b1', shape=[1],
                                                       initializer=tf.constant_initializer(value=0, dtype=tf.float32), dtype=tf.float32)
            # 全连接层，把 h_i 转为 u_i ， shape= [batch_size, units, input_size] -> [batch_size, units, output_size]
            input_projection = layers.fully_connected(inputs, output_size, activation_fn=activation_fn, scope=scope)
            # 输出 [batch_size, units]
            a = tf.multiply(input_projection, atten_w1) + atten_b1
            #a2 = tf.multiply(a1, atten_w2) + atten_b2
            vector_attn = tf.reduce_sum(a, axis=2, keep_dims=True)
            attention_weights = tf.nn.softmax(vector_attn, dim=1)
            tf.summary.histogram('attention_weigths', attention_weights)
            weighted_projection = tf.multiply(inputs, attention_weights)
            outputs = tf.reduce_sum(weighted_projection, axis=1)
            return outputs  # 输出 [batch_size, hidden_size*2]

    def bigru_inference(self, X_inputs):
        inputs = tf.nn.embedding_lookup(self.embedding, X_inputs)
        inputs = tf.nn.dropout(x=inputs, keep_prob=self._keep_prob)
        output_bigru = self.bi_gru(inputs)
        output_att = self.task_specific_attention(output_bigru, self.hidden_size*2)
        return output_att
