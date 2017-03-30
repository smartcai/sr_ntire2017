import tensorflow as tf
from tensorflow.python.ops import data_flow_ops
import util

def average_gradients(graph_grads):
        average_grads = []
        for grad_and_vars in zip(*graph_grads):
            v = grad_and_vars[0][1]
            # sum
            grad = tf.add_n([x[0] for x in grad_and_vars])
            # average
            grad = grad / float(len(graph_grads))
            grad_and_var = (grad, v)
            average_grads.append(grad_and_var)
        return average_grads

flags = tf.app.flags
FLAGS = flags.FLAGS

flags.DEFINE_string('data_name', 'data_resize', 'Directory to put the training data.')
flags.DEFINE_string('hr_flist', 'flist/hr_debug.flist', 'file_list put the training data.')
flags.DEFINE_string('lr_flist', 'flist/lrX2_debug.flist', 'Directory to put the training data.')
flags.DEFINE_integer('scale', '2', 'batch size for training')
flags.DEFINE_string('model_name', 'model_conv', 'Directory to put the training data.')
flags.DEFINE_string('model_file_in', 'tmp/model_conv', 'Directory to put the training data.')
flags.DEFINE_string('model_file_out', 'tmp/model_conv', 'Directory to put the training data.')
flags.DEFINE_float('learning_rate', '0.001', 'Learning rate for training')
flags.DEFINE_integer('batch_size', '32', 'batch size for training')
# flags.DEFINE_list('gpu_list', [0,1], 'gpu list for multi-gpu training')

data = __import__(FLAGS.data_name)
model = __import__(FLAGS.model_name)
if ((data.resize_func is None) != model.upsample):
    print("Config Error")
    quit()

# gpul = list(__import__(FLAGS.gpu_list))
gpul = [0,1]



with tf.Graph().as_default():
    with tf.device('/cpu:0'):
        target_patches, source_patches = data.dataset(FLAGS.hr_flist, FLAGS.lr_flist, FLAGS.scale)
        target_batch_staging, source_batch_staging = tf.train.shuffle_batch([target_patches, source_patches], FLAGS.batch_size, 32768, 8192, num_threads=4, enqueue_many=True)
    stager = data_flow_ops.StagingArea([tf.float32, tf.float32], shapes=[[None, None, None, 3], [None, None, None, 3]])
    stage = stager.put([target_batch_staging, source_batch_staging])
    target_batch, source_batch = stager.get()

    optimizer = tf.train.AdamOptimizer(FLAGS.learning_rate)

    losses = []
    grads = []
    for idx, gpu in enumerate(gpul):
        with tf.device('/gpu:{}'.format(gpu)), tf.variable_scope('main_graph', reuse=idx>0):
            print('building graph on gpu {}'.format(gpu))
            predict_batch = model.build_model(source_batch, FLAGS.scale, True, reuse=idx>0)
            target_cropped_batch = util.crop_center(target_batch, tf.shape(predict_batch)[1:3])
            loss = tf.losses.mean_squared_error(target_cropped_batch, predict_batch)
            grad = optimizer.compute_gradients(loss)
            losses.append(loss)
            grads.append(grad)

    grads = average_gradients(grads)
    train_op = optimizer.apply_gradients(grads)
    # train_op = optimizer.minimize(loss)

    init = tf.global_variables_initializer()
    init_local = tf.local_variables_initializer()
    saver = tf.train.Saver()
    loss_acc = .0
    acc = 0
    config = tf.ConfigProto(allow_soft_placement=True)
    config.gpu_options.allow_growth = True
    with tf.Session(config=config) as sess:
        sess.run(init_local)
        if (tf.gfile.Exists(FLAGS.model_file_out) or tf.gfile.Exists(FLAGS.model_file_out + '.index')):
            print('Model exists')
            quit()
        if (tf.gfile.Exists(FLAGS.model_file_in) or tf.gfile.Exists(FLAGS.model_file_in + '.index')):
            saver.restore(sess, FLAGS.model_file_in)
            print('Model restored from ' + FLAGS.model_file_in)
        else:
            sess.run(init)
            print('Model initialized')
        coord = tf.train.Coordinator()
        threads = tf.train.start_queue_runners(sess=sess, coord=coord)
        try:
            sess.run(stage)
            while not coord.should_stop():
                _, _, training_loss = sess.run([stage, train_op, loss])
                print(training_loss)
                loss_acc += training_loss
                acc += 1
                if (acc % 100000 == 0):
                    saver.save(sess, FLAGS.model_file_out + '-' + str(acc))
        except tf.errors.OutOfRangeError:
            print('Done training -- epoch limit reached')
        finally:
            coord.request_stop()
        print('Average loss: ' + str(loss_acc / acc))
        saver.save(sess, FLAGS.model_file_out)
        print('Model saved to ' + FLAGS.model_file_out)