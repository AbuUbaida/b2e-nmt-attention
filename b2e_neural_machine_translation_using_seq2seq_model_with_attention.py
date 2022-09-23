# -*- coding: utf-8 -*-
"""B2E Neural Machine Translation using Seq2Seq model with Attention.ipynb

Author: abu.ubaida1206@gmail.com

Original file is located at
    https://colab.research.google.com/drive/16L47i7pmmcERgdnFCBl2zHTMPJwFno9o
"""

import torch
import torch.functional as F
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence

import pandas as pd
from sklearn.model_selection import train_test_split
import numpy as np
import unicodedata
import re
import time

print(torch.__version__)

# Converts the unicode file to ascii
def unicode_to_ascii(s):
    """
    Normalizes latin chars with accent to their canonical decomposition
    """
    return ''.join(c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn')

def preprocess_sentence(w):
    w = unicode_to_ascii(w.lower().strip())
    
    # creating a space between a word and the punctuation following it
    # eg: "he is a boy." => "he is a boy ." 
    # Reference:- https://stackoverflow.com/questions/3645931/python-padding-punctuation-with-white-spaces-keeping-punctuation
    w = re.sub(r"([?.!,¿।,])", r" \1 ", w)
    w = re.sub(r'[" "]+', " ", w)
    
    w = w.rstrip().strip()
    
    # adding a start and an end token to the sentence
    # so that the model know when to start and stop predicting.
    w = '<start> ' + w + ' <end>'
    return w

# This class creates a word -> index mapping (e.g,. "dad" -> 5) and vice-versa 
# (e.g., 5 -> "dad") for each language,
class LanguageIndex():
    def __init__(self, lang):
        """ lang are the list of phrases from each language"""
        self.lang = lang
        self.word2idx = {}
        self.idx2word = {}
        self.vocab = set()
        
        self.create_index()
        
    def create_index(self):
        for phrase in self.lang:
            # update with individual tokens
            self.vocab.update(phrase.split(' '))
            
        # sort the vocab
        self.vocab = sorted(self.vocab)

        # add a padding token with index 0
        self.word2idx['<pad>'] = 0
        
        # word to index mapping
        for index, word in enumerate(self.vocab):
            self.word2idx[word] = index + 1 # +1 because of pad token
        
        # index to word mapping
        for word, index in self.word2idx.items():
            self.idx2word[index] = word

def max_length(tensor):
    return max(len(t) for t in tensor)

def pad_sequences(x, max_len):
    padded = np.zeros((max_len), dtype=np.int64)
    if len(x) > max_len: padded[:] = x[:max_len]
    else: padded[:len(x)] = x
    return padded

# conver the data to tensors and pass to the Dataloader 
# to create an batch iterator

class MyData(Dataset):
    def __init__(self, X, y):
        self.data = X
        self.target = y
        # TODO: convert this into torch code is possible
        self.length = [ np.sum(1 - np.equal(x, 0)) for x in X]
        
    def __getitem__(self, index):
        x = self.data[index]
        y = self.target[index]
        x_len = self.length[index]
        return x,y,x_len
    
    def __len__(self):
        return len(self.data)

class Encoder(nn.Module):
    def __init__(self, vocab_size, embedding_dim, enc_units, batch_sz):
        super(Encoder, self).__init__()
        self.batch_sz = batch_sz
        self.enc_units = enc_units
        self.vocab_size = vocab_size
        self.embedding_dim = embedding_dim
        self.embedding = nn.Embedding(self.vocab_size, self.embedding_dim)
        self.gru = nn.GRU(self.embedding_dim, self.enc_units)
        
    def forward(self, x, lens, device):
        # x: batch_size, max_length 
        
        # x: batch_size, max_length, embedding_dim
        x = self.embedding(x) 
                
        # x transformed = max_len X batch_size X embedding_dim
        # x = x.permute(1,0,2)
        x = pack_padded_sequence(x, lens) # unpad
    
        self.hidden = self.initialize_hidden_state(device)
        
        # output: max_length, batch_size, enc_units
        # self.hidden: 1, batch_size, enc_units
        output, self.hidden = self.gru(x, self.hidden) # gru returns hidden state of all timesteps as well as hidden state at last timestep
        
        # pad the sequence to the max length in the batch
        output, _ = pad_packed_sequence(output)
        
        return output, self.hidden

    def initialize_hidden_state(self, device):
        return torch.zeros((1, self.batch_sz, self.enc_units)).to(device)

### sort batch function to be able to use with pad_packed_sequence
def sort_batch(X, y, lengths):
    lengths, indx = lengths.sort(dim=0, descending=True)
    X = X[indx]
    y = y[indx]
    return X.transpose(0,1), y, lengths # transpose (batch x seq) to (seq x batch)

class Decoder(nn.Module):
    def __init__(self, vocab_size, embedding_dim, dec_units, enc_units, batch_sz):
        super(Decoder, self).__init__()
        self.batch_sz = batch_sz
        self.dec_units = dec_units
        self.enc_units = enc_units
        self.vocab_size = vocab_size
        self.embedding_dim = embedding_dim
        self.embedding = nn.Embedding(self.vocab_size, self.embedding_dim)
        self.gru = nn.GRU(self.embedding_dim + self.enc_units, 
                          self.dec_units,
                          batch_first=True)
        self.fc = nn.Linear(self.enc_units, self.vocab_size)
        
        # used for attention
        self.W1 = nn.Linear(self.enc_units, self.dec_units)
        self.W2 = nn.Linear(self.enc_units, self.dec_units)
        self.V = nn.Linear(self.enc_units, 1)
    
    def forward(self, x, hidden, enc_output):
        # enc_output original: (max_length, batch_size, enc_units)
        # enc_output converted == (batch_size, max_length, hidden_size)
        enc_output = enc_output.permute(1,0,2)
        # hidden shape == (batch_size, hidden size)
        # hidden_with_time_axis shape == (batch_size, 1, hidden size)
        # we are doing this to perform addition to calculate the score
        
        # hidden shape == (batch_size, hidden size)
        # hidden_with_time_axis shape == (batch_size, 1, hidden size)
        hidden_with_time_axis = hidden.permute(1, 0, 2)
        
        # score: (batch_size, max_length, hidden_size) # Bahdanaus's
        # we get 1 at the last axis because we are applying tanh(FC(EO) + FC(H)) to self.V
        # It doesn't matter which FC we pick for each of the inputs
        score = torch.tanh(self.W1(enc_output) + self.W2(hidden_with_time_axis))
        
        #score = torch.tanh(self.W2(hidden_with_time_axis) + self.W1(enc_output))
          
        # attention_weights shape == (batch_size, max_length, 1)
        # we get 1 at the last axis because we are applying score to self.V
        attention_weights = torch.softmax(self.V(score), dim=1)
        
        # context_vector shape after sum == (batch_size, hidden_size)
        context_vector = attention_weights * enc_output
        context_vector = torch.sum(context_vector, dim=1)
        
        # x shape after passing through embedding == (batch_size, 1, embedding_dim)
        # takes case of the right portion of the model above (illustrated in red)
        x = self.embedding(x)
        
        # x shape after concatenation == (batch_size, 1, embedding_dim + hidden_size)
        #x = tf.concat([tf.expand_dims(context_vector, 1), x], axis=-1)
        # ? Looks like attention vector in diagram of source
        x = torch.cat((context_vector.unsqueeze(1), x), -1)
        
        # passing the concatenated vector to the GRU
        # output: (batch_size, 1, hidden_size)
        output, state = self.gru(x)
        
        
        # output shape == (batch_size * 1, hidden_size)
        output =  output.view(-1, output.size(2))
        
        # output shape == (batch_size * 1, vocab)
        x = self.fc(output)
        
        return x, state, attention_weights
    
    def initialize_hidden_state(self):
        return torch.zeros((1, self.batch_sz, self.dec_units))

def loss_function(real, pred):
    """ Only consider non-zero inputs in the loss; mask needed """
    #mask = 1 - np.equal(real, 0) # assign 0 to all above 0 and 1 to all 0s
    #print(mask)
    mask = real.ge(1).type(torch.cuda.FloatTensor)
    
    loss_ = criterion(pred, real) * mask 
    return torch.mean(loss_)

f = open('../data/ben.txt', encoding='UTF-8').read().strip().split('\n')
lines = f

num_examples = 4642 

# creates lists containing each pair
original_word_pairs = [[w for w in l.split('\t')] for l in lines[:num_examples]]

data = pd.DataFrame(original_word_pairs, columns=["en", "bn", "extra"])
data = data.drop(["extra"], axis=1)

# Now we do the preprocessing using pandas and lambdas
data["en"] = data.en.apply(lambda w: preprocess_sentence(w))
data["bn"] = data.bn.apply(lambda w: preprocess_sentence(w))

# index language using the class above
inp_lang = LanguageIndex(data["bn"].values.tolist())
targ_lang = LanguageIndex(data["en"].values.tolist())
# Vectorize the input and target languages
input_tensor = [[inp_lang.word2idx[s] for s in bn.split(' ')]  for bn in data["bn"].values.tolist()]
target_tensor = [[targ_lang.word2idx[s] for s in en.split(' ')]  for en in data["en"].values.tolist()]

# calculate the max_length of input and output tensor
max_length_inp, max_length_tar = max_length(input_tensor), max_length(target_tensor)

# inplace padding
input_tensor = [pad_sequences(x, max_length_inp) for x in input_tensor]
target_tensor = [pad_sequences(x, max_length_tar) for x in target_tensor]

# Creating training and validation sets using an 80-20 split
input_tensor_train, input_tensor_val, target_tensor_train, target_tensor_val = train_test_split(input_tensor, target_tensor, test_size=0.2)

BUFFER_SIZE = len(input_tensor_train)
BATCH_SIZE = 64
N_BATCH = BUFFER_SIZE//BATCH_SIZE
embedding_dim = 256
units = 1024
vocab_inp_size = len(inp_lang.word2idx)
vocab_tar_size = len(targ_lang.word2idx)

train_dataset = MyData(input_tensor_train, target_tensor_train)
val_dataset = MyData(input_tensor_val, target_tensor_val)

dataset = DataLoader(train_dataset, batch_size = BATCH_SIZE, drop_last=True, shuffle=True)

device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
encoder = Encoder(vocab_inp_size, embedding_dim, units, BATCH_SIZE)

encoder.to(device)
# obtain one sample from the data iterator
it = iter(dataset)
x, y, x_len = next(it)

# sort the batch first to be able to use with pac_pack_sequence
xs, ys, lens = sort_batch(x, y, x_len)

enc_output, enc_hidden = encoder(xs.to(device), lens, device)

# Device
device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
encoder = Encoder(vocab_inp_size, embedding_dim, units, BATCH_SIZE)

encoder.to(device)
# obtain one sample from the data iterator
it = iter(dataset)
x, y, x_len = next(it)

# sort the batch first to be able to use with pac_pack_sequence
xs, ys, lens = sort_batch(x, y, x_len)

enc_output, enc_hidden = encoder(xs.to(device), lens, device)
print("Encoder Output: ", enc_output.shape) # batch_size X max_length X enc_units
print("Encoder Hidden: ", enc_hidden.shape) # batch_size X enc_units (corresponds to the last state)

decoder = Decoder(vocab_tar_size, embedding_dim, units, units, BATCH_SIZE)
decoder = decoder.to(device)

#print(enc_hidden.squeeze(0).shape)

dec_hidden = enc_hidden#.squeeze(0)
dec_input = torch.tensor([[targ_lang.word2idx['<start>']]] * BATCH_SIZE)
print("Decoder Input: ", dec_input.shape)
print("--------")

for t in range(1, y.size(1)):
    # enc_hidden: 1, batch_size, enc_units
    # output: max_length, batch_size, enc_units
    predictions, dec_hidden, _ = decoder(dec_input.to(device), 
                                         dec_hidden.to(device), 
                                         enc_output.to(device))
    
    print("Prediction: ", predictions.shape)
    print("Decoder Hidden: ", dec_hidden.shape)
    
    #loss += loss_function(y[:, t].to(device), predictions.to(device))
    
    dec_input = y[:, t].unsqueeze(1)
    print(dec_input.shape)
    break

criterion = nn.CrossEntropyLoss()

# Device
device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

encoder = Encoder(vocab_inp_size, embedding_dim, units, BATCH_SIZE)
decoder = Decoder(vocab_tar_size, embedding_dim, units, units, BATCH_SIZE)

encoder.to(device)
decoder.to(device)

optimizer = optim.Adam(list(encoder.parameters()) + list(decoder.parameters()), lr=0.001)

EPOCHS = 10

for epoch in range(EPOCHS):
    start = time.time()
    
    encoder.train()
    decoder.train()
    
    total_loss = 0
    
    for (batch, (inp, targ, inp_len)) in enumerate(dataset):
        loss = 0
        
        xs, ys, lens = sort_batch(inp, targ, inp_len)
        enc_output, enc_hidden = encoder(xs.to(device), lens, device)
        dec_hidden = enc_hidden
        
        # use teacher forcing - feeding the target as the next input (via dec_input)
        dec_input = torch.tensor([[targ_lang.word2idx['<start>']]] * BATCH_SIZE)
        
        # run code below for every timestep in the ys batch
        for t in range(1, ys.size(1)):
            predictions, dec_hidden, _ = decoder(dec_input.to(device), 
                                         dec_hidden.to(device), 
                                         enc_output.to(device))
            loss += loss_function(ys[:, t].to(device), predictions.to(device))
            #loss += loss_
            dec_input = ys[:, t].unsqueeze(1)
            
        
        batch_loss = (loss / int(ys.size(1)))
        total_loss += batch_loss
        
        optimizer.zero_grad()
        
        loss.backward()

        ### UPDATE MODEL PARAMETERS
        optimizer.step()
        
        if batch % 100 == 0:
            print('Epoch {} Batch {} Loss {:.4f}'.format(epoch + 1, batch, batch_loss.detach().item()))
        
    print('Epoch {} Loss {:.4f}'.format(epoch + 1, total_loss / N_BATCH))
    print('Time taken for 1 epoch {} sec\n'.format(time.time() - start))

