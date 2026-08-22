"""`LstmGeometricFeatureModel`, ported verbatim from the notebook.

Source of truth: `notebook/03_deployment_export.ipynb`, cell defining `class
LstmGeometricFeatureModel` (search that notebook for that string — cell numbers aren't
stable), from its "Assembling the Geometric rate feature layer + LSTM Model" step. This is the
full deployment artifact: raw per-frame MediaPipe outputs go in, a 6-class softmax over
drowsiness levels comes out.

Two behaviors worth knowing before calling this from `detector.py`:

- **It's already stateful — but only the raw-feature buffer is, not the LSTM itself.**
  `feature_buffer` is a `tf.Variable` of shape `(1, max_timesteps, num_features)` held
  *inside* the model. Every call shifts it left by one frame and appends the new one — so
  callers must invoke this once per incoming frame, not accumulate their own window first.
  `lstm_model` (see `02_model_training.ipynb`'s "LSTM Model Definition" cell, and the "Design
  Decision: Persistent (`stateful=True`) Hidden State" markdown cell right before it, which
  documents why persistent cross-call hidden state was evaluated and deliberately deferred) is
  a plain `tf.keras.Sequential` of two `LSTM` layers *without* `stateful=True`, so every call
  re-runs the full LSTM stack over all `max_timesteps` buffered frames from a fresh zero hidden
  state — there's no cheaper incremental RNN update happening. `feature_buffer` is what makes
  this model stateful, not the LSTM layers. There's no "wait for the window to fill" step either;
  the model is meaningful from the first call, though early predictions (buffer still mostly
  zero-padded) are less reliable until ~`max_timesteps` frames have been fed in.
- **`custom_objects` at load time.** Keras can't reconstruct a custom `call()` body from the
  saved file alone, so both this class and `GeometricRatioFeatureLayer` must be passed to
  `tf.keras.models.load_model(..., custom_objects={...})` — see `detector.py`.
"""

import tensorflow as tf

from .layers import GeometricRatioFeatureLayer


class LstmGeometricFeatureModel(tf.keras.Model):
    def __init__(self, ratio_layer: GeometricRatioFeatureLayer,
                 normalization_layer: tf.keras.layers.Normalization,
                 lstm_model: tf.keras.Model,
                 max_timesteps: int = 60,
                 num_features: int = 59, **kwargs):
        super().__init__(**kwargs)
        self.ratio_layer = ratio_layer
        self.normalization_layer = normalization_layer
        self.lstm_model = lstm_model
        self.max_timesteps = max_timesteps
        self.num_features = num_features

        # Initialize a buffer for the sequence history (for real-time inference)
        # Note: This buffer is stateful, so it's handled within the model for deployment.
        self.feature_buffer = tf.Variable(
            tf.zeros([1, self.max_timesteps, self.num_features], dtype=tf.float32),
            trainable=False,  # This buffer is not part of model training
            name="feature_buffer"
        )

        # Ensure blendshape names are available to the ratio_layer for consistent ordering
        if not hasattr(self.ratio_layer, 'blendshape_names'):
            raise AttributeError("ratio_layer must have 'blendshape_names' attribute defined.")

    def call(self, inputs, training=False):
        # inputs is a dictionary: {'landmarks': (1, 478, 2), 'rotation_matrix': (1, 3, 3), 'blendshapes': (1, 52)}
        landmarks = inputs['landmarks']
        rotation_matrix = inputs['rotation_matrix']
        raw_blendshapes = inputs['blendshapes']

        # 1. Geometric Feature Extraction
        geometric_features = self.ratio_layer(landmarks, rotation_matrix)

        # 2. Combine Geometric Features and Blendshapes
        # Ensure raw_blendshapes matches the expected order if necessary, but here we assume it's ordered.
        combined_features = tf.concat([geometric_features, raw_blendshapes], axis=-1)

        # Reshape to (1, 1, num_features) to represent a single frame's features
        single_frame_features = tf.reshape(combined_features, [1, 1, self.num_features])

        # 3. Update the feature buffer (sliding window)
        # Shift old features to the left, insert new feature at the right
        updated_buffer = tf.concat(
            [self.feature_buffer[:, 1:, :], single_frame_features],
            axis=1
        )
        self.feature_buffer.assign(updated_buffer)

        # Use the current state of the buffer as the sequence input for the LSTM
        sequence_input = self.feature_buffer

        # 4. Normalization
        normalized_sequence = self.normalization_layer(sequence_input)

        # 5. LSTM Prediction
        predictions = self.lstm_model(normalized_sequence, training=training)

        return predictions

    def get_config(self):
        config = super().get_config()
        config.update({
            "ratio_layer": tf.keras.utils.serialize_keras_object(self.ratio_layer),
            "normalization_layer": tf.keras.utils.serialize_keras_object(self.normalization_layer),
            "lstm_model": tf.keras.utils.serialize_keras_object(self.lstm_model),
            "max_timesteps": self.max_timesteps,
            "num_features": self.num_features,
        })
        return config

    @classmethod
    def from_config(cls, config):
        # Deserialize nested Keras objects
        ratio_layer_config = config.pop("ratio_layer")
        normalization_layer_config = config.pop("normalization_layer")
        lstm_model_config = config.pop("lstm_model")

        # Provide custom_objects for custom layers during deserialization
        ratio_layer = tf.keras.utils.deserialize_keras_object(
            ratio_layer_config, custom_objects={'GeometricRatioFeatureLayer': GeometricRatioFeatureLayer}
        )
        normalization_layer = tf.keras.utils.deserialize_keras_object(normalization_layer_config)
        lstm_model = tf.keras.utils.deserialize_keras_object(lstm_model_config)

        # Instantiate the class with the deserialized objects and remaining config
        return cls(ratio_layer=ratio_layer,
                    normalization_layer=normalization_layer,
                    lstm_model=lstm_model,
                    **config)
