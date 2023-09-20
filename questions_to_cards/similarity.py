import jellyfish as jf
import pandas as pd
import numpy as np
import string
import re
from unidecode import unidecode
from tqdm import tqdm

tqdm.pandas()
import time
from collections import Counter
import batch_jaro_winkler as bjw

CLUES_FILEPATH = 'test_output/clues_2023512-104755.csv'

qb_stopwords = {'a', 'an', 'and', 'of', 'the', 'this', 'these'}
more_stopwords = {'the', 'that', 'he', 'him', 'his', 'she', 'her', 'hers',
                  'is', 'are', 'work', 'works', 'who', 'which', 'was', 'were',
                  'one', 'another', 'as', 'in', 'when', 'they', 'their', 'them',
                  'name', 'identify', 'man', 'mans', 'from', 'on', 'to', 'by',
                  'with', 'title', 'titular', 'those', 'it', 'its', 'be', 'at',
                  'as'}
indicator_stopwords = {'figure', 'figures', 'entity', 'entities', 'object',
                       'objects', 'substance', 'substances', 'character',
                       'characters'}
ans_stopwords = {'accept', 'prompt', 'reject', 'directed', 'antiprompt',
                 'anti-prompt', 'or'}
all_stopwords = qb_stopwords | more_stopwords | indicator_stopwords
qb_punctuation = string.punctuation + '“”'

pd.set_option('display.max_colwidth', 1000)


def subset(clues, ans_term=None, clue_term=None, write_out=False):
    '''
    Generate subsets of a DataFrame for quicker similarity comparison.

    Inputs:
        - clues (string or DataFrame): can take a filepath string to import
        from filepath; otherwise, take an existing DataFrame of clues
        - ans_term (string or None): term that must be in answer line upon
        filtering.
        - clue_term (string or None): term that must be in clue upon
        filtering. As of now, ans_term filter is applied FIRST, and having
        a non-null value for both ans_term and clue_term produces the strict
        INTERSECTION in which both are present.
        #TODO: Consider altering behavior to allow for UNION/OR.
        - write_out (boolean): whether to write to file or not.

    Returns (pandas DataFrame): the subset you want.
    '''
    if type(clues) == str:
        clues = pd.read_csv(clues, sep='\t')

    assert type(clues) == pd.core.frame.DataFrame, "You don't have a working df"
    # TODO: fix ValueError
    if ans_term is None and clue_term is None:
        return clues

    subset = clues

    if ans_term is not None:
        ans_term = ans_term.lower()
        print(ans_term)
        subset = subset.loc[(subset.loc[:, 'answer'].str.contains(ans_term, flags=re.IGNORECASE)) &
                            (~subset.loc[:, 'answer'].isna()), :]  # some answers are 'nan'
        subset.reset_index(drop=True, inplace=True)
    if clue_term is not None:
        print(clue_term)
        clue_term = clue_term.lower()
        subset = subset.loc[(subset.loc[:, 'clue'].str.contains(clue_term, flags=re.IGNORECASE)), :]
        subset.reset_index(drop=True, inplace=True)

    if write_out:
        write_filepath = f'subset_{ans_term}_{clue_term}.csv'
        subset.to_csv(write_filepath, sep='\t', escapechar='\\', index=False)
    return subset


def distill(
        phrase: str,
        answerline=False,
        remove_brackets=True,
        max_length=50) -> str:
    '''
    Distill a clue or answer line down by removing stopwords, spaces, and
    punctuation to make Jaro-Winkler string distance score more robust.
    If it's an answer line, removes acceptable/promptable answers to expand
    range of matching.
    '''
    if type(phrase) != str:
        phrase = str(phrase)

    if answerline:
        # phrase = re.sub(r'\(.+\)|\[.+\]', '', phrase)
        REJECT_RE = r'(?:do not|don’t)\s(?:accept|prompt|take)\s|reject\s'
        # get rid of everything after reject/do not accept
        phrase = re.split(REJECT_RE, phrase)[0]

    if remove_brackets:
        phrase = re.sub(r'\[[^\[]+\]|\([^\(]+\)|{[^\{]+}', '', phrase)

    try:
        phrase = re.sub(r'[^\w\s\d]', '', unidecode(phrase.lower()))
    except AttributeError:  # it gets 'nan' sometimes which messes it up
        phrase = re.sub(r'[^\w\s\d]', '', unidecode(phrase.lower()))
    phrase = [word for word in phrase.split() if word not in qb_stopwords]
    if answerline:
        phrase = [word for word in phrase if word not in ans_stopwords]

    distilled_phrase = ''.join(phrase)
    if len(distilled_phrase) > max_length:
        distilled_phrase = distilled_phrase[:max_length + 1]

    return distilled_phrase


def unique_simple_answerlines(filepath=CLUES_FILEPATH):
    '''
    Determine how many unique answerlines there are in a clue DataFrame.
    '''
    df = pd.read_csv(filepath, sep='\t')
    df.loc[:, 'simple_answer'] = df.loc[:, 'answer'].progress_apply(lambda x: distill(str(x), answerline=True))
    df.drop_duplicates(subset=['simple_answer'], inplace=True)
    df = df.sort_values('simple_answer', ascending=True)
    series = df.loc[:, 'simple_answer']
    print(series)
    series.to_csv('unique_answers_0512.csv', sep='\t', escapechar='\\', index=False)


def wordify(clue: str, answerline=False):
    '''
    Convert a sentence/clue/answer into a set of unique non-stopword words.
    This prepares the input for Jaccard or overlap similarity comparisons.
    '''
    if answerline:
        # clue = re.sub(r'\(.+\)|\[.+\]', '', clue)
        REJECT_RE = r'(?:do not|don’t)\s(?:accept|prompt|take)\s|reject\s'
        # get rid of everything after reject/do not accept
        clue = re.split(REJECT_RE, clue)[0]

    clue = re.sub(r'[^\w\s\d]', '', unidecode(clue.lower()))
    # consider doing some lemmatizing here
    word_set = {wd for wd in clue.split() if wd not in all_stopwords}
    if answerline:
        return {wd for wd in word_set if wd not in ans_stopwords}
    else:
        return word_set


def overlap(clue1, clue2, debug=False):
    '''
    Calculate the overlap coefficient of two clues.
    See https://en.wikipedia.org/wiki/Overlap_coefficient
    '''
    bag1 = wordify(clue1)
    bag2 = wordify(clue2)
    shared = bag1 & bag2

    try:
        overlap_coefficient = len(shared) / min(len(bag1), len(bag2))
    except ZeroDivisionError:
        overlap_coefficient = 1

    if debug:
        print(f"Shared: {len(shared)}")
        print(f"Bag 1 size: {len(bag1)}; Bag 2 size: {len(bag2)}")
        print(f"Overlap coefficient: {overlap_coefficient}")
    return overlap_coefficient


def overlap_cb(clue_bag1, clue_bag2, debug=False):
    '''
    Calculate the overlap coefficient of two clues.
    See https://en.wikipedia.org/wiki/Overlap_coefficient
    '''
    shared = clue_bag1 & clue_bag2

    try:
        overlap_coefficient = len(shared) / min(len(clue_bag1), len(clue_bag2))
    except ZeroDivisionError:
        overlap_coefficient = 1

    if debug:
        print(f"Shared: {len(shared)}")
        print(f"Bag 1 size: {len(clue_bag1)}; Bag 2 size: {len(clue_bag2)}")
        print(f"Overlap coefficient: {overlap_coefficient}")
    return overlap_coefficient


def overlap_compare(clue1, clue2, threshold=0.6, debug=False):
    '''
    Determine whether two clues' overlap coefficient is above desired threshold.
    '''
    if debug:
        print(f"Threshold: {threshold}")
    return (overlap(clue1, clue2, debug=debug) >= threshold)


def comparator_test(
        func=jf.jaro_distance,
        str1="Name this American poet of “Lady Lazarus,” “Daddy,” and The Bell Jar.",
        str2="Name this poet of “Ariel” and “Daddy” as well as The Bell Jar."
):
    return func(str1, str2)


def naive_remove_redundancies(
        clue_df,
        ANS_THRESH=0.7,
        CLUE_THRESH=0.6,
):
    '''
    Generate a "simplified answer line" for every row.
    Then for each row in the dataframe, compare that row against all (remaining)
    rows later than it. If their answers have similar enough Jaro-Winkler similarity
    and the clues have a high enough percentage of overlapping words, mark
    the row with the shorter clue for deletion

    This function is for prototyping purposes only, to demonstrate the desired
    logic for row similarity comparison and removal. It will be extremely slow
    on large Pandas dataframes -- i.e. O(n^2).
    Estimated run time for a dataframe of 10,000 clues is 30 minutes.
    Estimated run time for a dataframe of 1.4 million clues is 243 days.
    '''
    # TODO: be more sure this isn't altering original df
    clue_df = clue_df.copy(deep=True)
    clue_df.loc[:, 'simple_answer'] = clue_df.loc[:, 'answer'].progress_apply(
        lambda x: distill(str(x), answerline=True)
    )

    rows_to_delete = 0
    for idx, row_a in clue_df.iterrows():
        print(f"\nStarting row {idx}")
        if row_a.clue == '_DEL_':
            print(f"Row {idx} has already been marked for deletion. Continuing")
            continue

        for jdx, row_b in clue_df.loc[idx + 1:, :].iterrows():
            if row_b.clue == '_DEL_':
                print(f"Row {jdx} has already been marked for deletion. Continuing")
                continue

            print(f"Comparing row {idx} to row {jdx}")
            print(f"Overlap of {row_a.simple_answer}, {row_b.simple_answer}:")
            answer_similarity = jf.jaro_distance(row_a.simple_answer, row_b.simple_answer)
            print(answer_similarity)

            if answer_similarity > ANS_THRESH:
                print(f"\nOverlap of clues...")
                print(row_a.clue)
                print(row_b.clue)
                clue_similarity = overlap(row_a.clue, row_b.clue)
                print(clue_similarity)

                if clue_similarity > CLUE_THRESH:
                    row_a_size = len(wordify(row_a.clue))
                    row_b_size = len(wordify(row_b.clue))
                    print(f"Row {idx} size: {row_a_size}; Row {jdx} size: {row_b_size}")
                    if row_a_size > row_b_size:
                        print(f"Row {jdx} has fewer words. Delete it")
                        clue_df.loc[jdx, 'clue'] = '_DEL_'
                        clue_df.loc[jdx, 'answer'] = '_DEL_'
                        rows_to_delete += 1
                    elif row_a_size == row_b_size:
                        print(f"Rows {idx} and {jdx} have the same number of unique words. Continue")
                        continue
                    elif row_b_size > row_a_size:
                        print(f"Row {idx} has fewer words. Delete it and move on to next row")
                        clue_df.loc[idx, 'clue'] = '_DEL_'
                        clue_df.loc[idx, 'answer'] = '_DEL_'
                        rows_to_delete += 1
                        break
            else:
                print("Not similar enough. Continuing\n")

    print(f"{rows_to_delete} rows marked for deletion.")
    clue_df = clue_df.loc[~(clue_df.loc[:, 'clue'] == '_DEL_'), :]
    print(f"Deletion complete. {len(clue_df)} rows remain.")
    return clue_df

def remove_redundancies(
        clue_df,
        ans_term=None,
        clue_term=None,
        start_at=None,
        end_at=None,
        skip_thresh=None,
        ans_thresh=0.7,
        clue_thresh=0.6,
        ans_func=jf.jaro_distance,
        clue_func=overlap_cb,
        simplify_answers=True,
        asc=True
):
    '''
    Most up-to-date function for finding repetitious clues and deleting them
    to minimize redunancy in final deck of cards.

    Starts by creating a simplified answer line and calculating the "bag size"
    (number of unique non-stopword words in the clue) for all rows.

    Then, for each row of the dataframe, uses Pandas selectors and vectorized
    .apply() to do the following:
        -"Block" on fuzzy-matching answer lines by finding LATER rows whose
        answer line has a high enough Jaro-Winkler similarity score to current row.
        -Within those, find rows whose clue has high enough word overlap with
        current row.
        -Mark current row for deletion if any high-word-overlap clue below this
        one is longer than current row (to preserve card with maximal information).
        -Mark any row with fuzzy-matching answer and high-word-overlap clue for
        deletion if that row's clue is shorter than current row (to delete redundancies).

    Inputs:
        -clues_filepath (str or DataFrame): location of clues DataFrame in directory
        or the DataFrame itself. (#TODO: make flexible to take df from other sources)
        -term (str): used for subsetting the DataFrame to look only at clues
        that contain this substring. Greatly increases runtime.
        -start_at (str or None): used to subset the DataFrame to look only at
        answerlines that start after this point (e.g., 'start_at=aarom' allows
        for starting at the answer line 'Aaron'.) (#TODO: Change this so it merely
        STARTS AT this answer rather than DELETING prior rows.) (#TODO: allow this
        to be an int representing an index.)
        -end_at (str or None): used to subset the DataFrame to look only at
        answerlines up to this point (e.g., 'end_at=jaws' allows for ending
        redundancy removal at the answer line 'Jaws')
        -skip_thresh (int or None): if an integer, represents the minimum number
        of occurrences a simple answer should have in order to be evaluated. For
        example, if skip_thresh == 3, the function will not recalculate similarity
        scores for simple answers that occur only 2 times or 1 time in the
        underlying df. This saves time when the clue df is large and full of
        relatively rare answer lines that are unlikely to have matching clues.
        -ans_thresh (float): threshold value for answer similarity score, above
        which two answers will be considered to match.
        -clue_thresh (float): thresold value for clue similarity score, above
        which two clues will be considere close enough to mark the shorter one
        for deletion.
        -ans_func (function name): Should be Jaro-Winkler distance; can be changed
        to test a different similarity function.
        -clue_func (function name): Should be overlap; can be changed to test
        a different similarity function.
        -simplify_answers (boolean): Determines whether answers are simplified
        prior to comparison. Should be set to True.
        -asc (boolean): Determines whether simplified answer lines are sorted
        alphabetically (0-Z, True) or in reverse alphabetical order (Z-0, False).

    Returns (df): the dataframe with repetitious rows deleted.
    '''
    if ans_term is not None or clue_term is not None:
        print("Subsetting dataframe...")
    df = subset(clue_df, ans_term, clue_term)
    #
    # print("Generating simplified answer lines for every row...")
    # if simplify_answers:
    #     df.loc[:,'simple_answer'] = df.loc[:,'answer'].progress_apply(
    #         lambda x:distill(str(x), answerline=True)
    #         )
    # else:
    #     df.loc[:,'simple_answer'] = df.loc[:,'answer']
    #
    # print("Counting frequency of each simplified answer...")
    simple_ans_freqs = Counter(df.loc[:, 'simple_answer'])






    print("generating clue bag...")
    df.loc[:, 'clue_bag'] = df.loc[:, 'clue'].progress_apply(wordify)



    #
    # print("Calculating number of unique words in each clue...")
    # df.loc[:,'bag_size'] = df.loc[:,'clue'].progress_apply(
    #     lambda x:len(wordify(x))
    # )
    #
    # # greatly reduce runtime, by allowing us to calculate all matches for each
    # # simple answerline only once.
    print("Sorting database...")
    df = df.sort_values(by=['simple_answer', 'clue'], ascending=asc)
    #
    # df.reset_index(drop=True, inplace=True)
    print(len(df))
    df = df.dropna(how="any", subset=["answer", "simple_answer"]).reset_index(drop=True)
    print(len(df))

    print("Generating numeric_clue_bag table...")

    bag_size_numpy = df["bag_size"].to_numpy()
    word_counter = Counter()
    for clue_bag in df["clue_bag"]:
        word_counter.update(clue_bag)
    all_word_arr = np.sort(np.array([word for word, occs in word_counter.items() if occs > 0]))
    numeric_clue_bag = np.zeros((len(df), np.amax(bag_size_numpy)), dtype=int)-1
    for clue_i, clue_bag in enumerate(tqdm(df["clue_bag"])):
        idxs = np.searchsorted(all_word_arr, np.array(list(clue_bag)))
        numeric_clue_bag[clue_i, :len(idxs)] = idxs




    df.loc[:, 'ans_similarity'] = -1.0
    df.loc[:, 'clue_similarity'] = -1.0

    uq_strs, uq_idxs = np.unique(df[["simple_answer"]].to_numpy().flatten(), return_inverse=True)
    uq_strs_df = pd.DataFrame(uq_strs, columns=["simple_answer"], dtype=str)
    print("doing bjw stuff...")
    exp_model = bjw.build_exportable_model(uq_strs.flatten())
    rt_model = bjw.build_runtime_model(exp_model)
    print("done with bjw stuff")

    prev_answer = None

    rows_marked_del = 0
    # for uq_i, uq_str in enumerate(uq_strs):
    #     res = bjw.jaro_distance(rt_model, uq_str)
    #     uq_res_vals = np.array([restup[1] for restup in res])
    ans_similarity_bin = np.full((len(df),), False)
    for idx, row in df.iterrows():

        print(f"\nNOW CONSIDERING ROW {idx}.")
        if idx > 5500:
            break
        # we can't check if row.clue == '_DEL_' because the underlying df mutates
        # as we go, but the iterrows() object does NOT mutate.
        if df.loc[idx, 'clue'] == '_DEL_':
            print(f"Row {idx} has been marked for deletion. Continuing")
            continue
        else:
            print(f"\nanswer: {row.answer}\nclue: {row.clue}")

        this_ans_freq = simple_ans_freqs[row.simple_answer]
        if skip_thresh is not None and this_ans_freq < skip_thresh:
            print(f"This answer occurs only {this_ans_freq} times. Not often enough to calculate scores")
            print("Skipping")
            continue

        if row.simple_answer != prev_answer:
            print("Recalculating similarity scores...")
            # Recalculate ans_similarity for all rows AFTER this one
            res = bjw.jaro_distance(rt_model, row.simple_answer)
            uq_res_vals = np.array([restup[1] for restup in res])
            #df.loc[:, 'ans_similarity'] = uq_res_vals[uq_idxs]
            ans_similarity_bin = (uq_res_vals > ans_thresh)[uq_idxs]
            # all_sims = uq_strs_df["simple_answer"].progress_apply(lambda x: ans_func(x, row.simple_answer))
            # df.loc[:, 'ans_similarity'] = all_sims.iloc[uq_idxs].reset_index()
            prev_answer = row.simple_answer
        ans_similarity_bin[:idx+1] = False

        # Get matching answers later than this one
        df_subset = df.loc[ans_similarity_bin, :]
        # Within that, get matching clues
        # TODO: Check if this is being calculated right. Values sometimes seem too small

        ncb_subset = numeric_clue_bag[ans_similarity_bin, :].copy()
        ncb_subset[ncb_subset < 0] = -2
        shared_words = np.sum(np.isin(ncb_subset, numeric_clue_bag[idx, :]), axis=1)
        min_vals = np.minimum(row.bag_size, bag_size_numpy[ans_similarity_bin])
        clue_overlap_vals = shared_words/min_vals
        clue_overlap_vals[min_vals<1] = 1
        CLUE_MATCH_MASK = clue_overlap_vals > clue_thresh

        if CLUE_MATCH_MASK.sum() > 0:

            # within those, use panda selectors to get ones where the wordify_bag_size <
            # this row's wordify_bag_size
            SMALLER_MASK = (df_subset.loc[:, 'bag_size'] < row.bag_size)
            SMALLER_SUBSET_MASK = CLUE_MATCH_MASK & SMALLER_MASK
            # DEL_MASK = ANS_MATCH_MASK & CLUE_MATCH_MASK & SMALLER_MASK & IDX_MASK
            if (num_subset_del := SMALLER_SUBSET_MASK.sum()) > 0:
                print(f"{num_subset_del} rows ready to be marked for deletion")
                # print(df.loc[DEL_MASK, :])
                # mark all such rows for deletion
                df.loc[ans_similarity_bin, ['clue', 'answer', 'simple_answer']].loc[SMALLER_SUBSET_MASK, :] = '_DEL_'
                df.loc[ans_similarity_bin, ['bag_size']].loc[SMALLER_SUBSET_MASK, :] = 0
                rows_marked_del += num_subset_del
            else:
                print("NO MATCHING CLUES OF SMALLER LENGTH FOUND")

            # within those, if ANY has a wordify_bag_size > this row's wordify_bag_size:
            BIGGER_MASK = (df_subset.loc[:, 'bag_size'] > row.bag_size)
            BIGGER_SUBSET_MASK = CLUE_MATCH_MASK & BIGGER_MASK
            if BIGGER_SUBSET_MASK.sum() > 0:
                # TODO: Show the clue(s) that are longer that necessitated deleting this one
                print("THIS ROW IS SHORTER THAN A MATCHING CLUE. MARKING IT FOR DELETION...")
                print("(For reference, here is a LONGER row we are KEEPING:)")
                print(df_subset.loc[BIGGER_SUBSET_MASK, :].sample(1))
                df.loc[idx, ['clue', 'answer', 'simple_answer']] = '_DEL_'
                df.loc[idx, 'bag_size'] = 0
                rows_marked_del += 1

        print(f"Rows marked for deletion so far: {rows_marked_del}")

    print(f"{rows_marked_del} total rows marked for deletion")
    df = df.loc[(df.loc[:, 'clue'] != '_DEL_'), ["clue", "answer", "tags"]]
    print("Redundant row deletion complete")
    return df


if __name__ == '__main__':
    # print("Loading clue csv...")
    clues = pd.read_csv("cluestest_cleaned.csv", sep="\t")
    # clues = pd.read_csv(CLUES_FILEPATH, sep='\t')
    # ans_input = input("Choose phrase to filter answer line by, or type Enter to continue:")
    # if ans_input == '':
    #     ans_input = None
    # clue_input = input("Choose phrase to filter clues by, or type Enter to continue:")
    # if clue_input == '':
    #     clue_input = None

    remove_redundancies(clues)