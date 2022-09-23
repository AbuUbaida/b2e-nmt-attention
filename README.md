In this experiment, machine translation was performed using a deep learning based approach and attention mechanism. Specifically, a sequence to sequence model was trained for Bengali to English translation. For this task, a [bilingual corpus](http://www.manythings.org/anki/) was utilized, which is formed with tab-delimited Bengali-English sentence pairs. [GRU](https://en.wikipedia.org/wiki/Gated_recurrent_unit) was incorporated in both of the encoder and decoder with attention layer. After learning the patterns in input language (_i.e._ Bengali), the encoder output and hidden state were passed to the decoder along with the start token for generating the output sequences (_i.e._ English). For deciding the next input to the decoder, teacher forcing was performed. The codebase **(PyTorch)** can be found [here](https://github.com/AbuUbaida/b2e-nmt-attention).