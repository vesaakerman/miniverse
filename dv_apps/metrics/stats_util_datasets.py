"""
Create metrics for Datasets.
This may be used for APIs, views with visualizations, etc.
"""
#from django.db.models.functions import TruncMonth  # 1.10
from collections import OrderedDict

from django.db import models
from django.db.models import F
from django.db.models import Q

from django.utils.encoding import python_2_unicode_compatible

from dv_apps.utils.date_helper import get_month_name_abbreviation,\
    get_month_name,\
    month_year_iterator,\
    TIMESTAMP_MASK

from dv_apps.datasets.models import Dataset, DatasetVersion, DatasetLinkingDataverse
from dv_apps.dataverses.models import Dataverse, DataverseLinkingDataverse

from dv_apps.datasetfields.models import DatasetField,\
    DatasetFieldValue, DatasetFieldType,\
    DatasetFieldControlledVocabularyValue,\
    ControlledVocabularyValue

from dv_apps.metrics.stats_util_base import StatsMakerBase, TruncYearMonth
from dv_apps.dvobjects.models import DvObject\
    , DTYPE_DATASET, DTYPE_DATAVERSE\
    , DVOBJECT_CREATEDATE_ATTR
from dv_apps.metrics.stats_result import StatsResult


class StatsMakerDatasets(StatsMakerBase):

    def __init__(self, **kwargs):
        """
        Start and end dates are optional.

        start_date = string in YYYY-MM-DD format
        end_date = string in YYYY-MM-DD format
        """
        super(StatsMakerDatasets, self).__init__(**kwargs)

    # ----------------------------
    #   Dataset counts (single number totals)
    # ----------------------------
    def get_dataset_count(self, **extra_filters):
        """
        Return the Dataset count
        """
        if self.was_error_found():
            return self.get_error_msg_return()

        filter_params = self.get_date_filter_params()
        if extra_filters:
            for k, v in extra_filters.items():
                filter_params[k] = v

        q = Dataset.objects.filter(**filter_params)
        sql_query = str(q.query)

        data_dict = OrderedDict()
        data_dict['count'] = q.count()
        data_dict['count_string'] = "{:,}".format(data_dict['count'])

        return StatsResult.build_success_result(data_dict, sql_query)


    def get_dataset_count_published(self):
        """
        Return the count of published Dataverses
        """
        return self.get_dataset_count(**self.get_is_published_filter_param())


    def get_dataset_count_unpublished(self):
        """
        Return the count of unpublished Dataverses
        """
        return self.get_dataset_count(**self.get_is_NOT_published_filter_param())

    # ----------------------------
    #   Dataset counts by create date
    # ----------------------------
    def get_dataset_counts_by_create_date(self, **extra_filters):
        """
        Get # of datasets created each month
        """
        if self.was_error_found():
            return self.get_error_msg_return()

        filter_params = self.get_date_filter_params()
        if extra_filters:
            for k, v in extra_filters.items():
                filter_params[k] = v

        return self.get_dataset_count_by_month(date_param=DVOBJECT_CREATEDATE_ATTR,\
            **extra_filters)


    def get_dataset_counts_by_create_date_published(self):
        """
        Get # of --PUBLISHED-- datasets created each month
        """
        return self.get_dataset_counts_by_create_date(**self.get_is_published_filter_param())


    def get_dataset_counts_by_create_date_unpublished(self):
        """
        Get # of --UNPUBLISHED-- datasets created each month
        """
        return self.get_dataset_counts_by_create_date(**self.get_is_NOT_published_filter_param())


    def get_dataset_counts_by_publication_date(self):
        """
        Get # of datasets published each month
        """
        return self.get_dataset_count_by_month(date_param='dvobject__publicationdate')


    def get_dataset_counts_by_modification_date(self):
        """
        Get # of datasets modified each month

        Not great b/c only the last modified date is recorded
        """
        return self.get_dataset_count_by_month(date_param='dvobject__modificationtime')


    def get_dataset_count_start_point(self, **extra_filters):
        """Get the startpoint when keeping a running total of file downloads"""

        start_point_filters = self.get_running_total_base_date_filters()
        if start_point_filters is None:
            return 0

        if extra_filters:
            for k, v in extra_filters.items():
                start_point_filters[k] = v

        return Dataset.objects.select_related('dvobject').filter(**start_point_filters).count()


    def get_dataset_count_by_month(self, date_param=DVOBJECT_CREATEDATE_ATTR, **extra_filters):
        """
        Return dataset counts by month
        """
        # Was an error found earlier?
        #
        if self.was_error_found():
            return self.get_error_msg_return()

        # -----------------------------------
        # (1) Build query filters
        # -----------------------------------

        # Exclude records where dates are null
        #   - e.g. a record may not have a publication date
        if date_param == DVOBJECT_CREATEDATE_ATTR:
            exclude_params = {}
        else:
            exclude_params = { '%s__isnull' % date_param : True}

        # Retrieve the date parameters
        #
        filter_params = self.get_date_filter_params()

        # Add extra filters from kwargs
        #
        if extra_filters:
            for k, v in extra_filters.items():
                filter_params[k] = v

        # -----------------------------------
        # (2) Construct query
        # -----------------------------------

        # add exclude filters date filters
        #
        ds_counts_by_month = Dataset.objects.select_related('dvobject'\
                            ).exclude(**exclude_params\
                            ).filter(**filter_params)

        # annotate query adding "month_year" and "cnt"
        #
        ds_counts_by_month = ds_counts_by_month.annotate(\
            yyyy_mm=TruncYearMonth('%s' % date_param)\
            ).values('yyyy_mm'\
            ).annotate(count=models.Count('dvobject_id')\
            ).values('yyyy_mm', 'count'\
            ).order_by('%syyyy_mm' % self.time_sort)

        # store query string
        sql_query = str(ds_counts_by_month.query)

        # -----------------------------------
        # (3) Format results
        # -----------------------------------
        # hold the running total count
        running_total = self.get_dataset_count_start_point(**extra_filters)
        formatted_records = []  # move from a queryset to a []

        for d in ds_counts_by_month:
            fmt_dict = OrderedDict()
            fmt_dict['yyyy_mm'] = d['yyyy_mm'].strftime('%Y-%m')
            fmt_dict['count'] = d['count']

            # running total
            running_total += d['count']
            fmt_dict['running_total'] = running_total
            # d['month_year'] = d['yyyy_mm'].strftime('%Y-%m')

            # Add year and month numbers
            fmt_dict['year_num'] = d['yyyy_mm'].year
            fmt_dict['month_num'] = d['yyyy_mm'].month

            # Add month name
            month_name_found, month_name_short = get_month_name_abbreviation(d['yyyy_mm'].month)

            if month_name_found:
                assume_month_name_found, fmt_dict['month_name'] = get_month_name(d['yyyy_mm'].month)
                fmt_dict['month_name_short'] = month_name_short
            else:
                # Log it!!!!!!
                pass

            # Add formatted record
            formatted_records.append(fmt_dict)

        data_dict = OrderedDict()
        data_dict['record_count'] = len(formatted_records)
        data_dict['records'] = formatted_records

        return StatsResult.build_success_result(data_dict, sql_query)


    def get_dataset_subject_counts_published(self):
        """Published Dataset counts by subject"""

        return self.get_dataset_subject_counts(**self.get_is_published_filter_param())


    def get_dataset_subject_counts_unpublished(self):
        """Unpublished Dataset counts by subject"""

        return self.get_dataset_subject_counts(\
                    **self.get_is_NOT_published_filter_param())



    def get_published_dataverses_without_content(self, **extra_filters):
        """For curation purposes: a list of all published dataverses that do
        not contain any datasets/content. A spreadsheet starting with the oldest
        dataverses is appreciated.  Based on @sekmiller's SQL query"""

        # Was an error found earlier?
        #
        if self.was_error_found():
            return self.get_error_msg_return()

        # -----------------------------------
        # Retrieve the date parameters - distinguish by create date
        # -----------------------------------
        filter_params = self.get_date_filter_params()
        filter_params.update(self.get_is_published_filter_param())

        # -----------------------------------
        # Retrieve ids of Dataverses to ~exclude~
        # -----------------------------------

        # Get DvObject Ids of:
        #  - Dataverses that contain Datasets
        #  - Dataverses that have an owner
        #
        id_set1 = DvObject.objects.filter(\
                        Q(dtype=DTYPE_DATASET) |\
                        Q(dtype=DTYPE_DATAVERSE, owner__isnull=False)
                        ).distinct('owner__id'\
                        ).values_list('owner__id', flat=True)

        # Get DvObject Ids of:
        #  - Dataverses that link to datasets
        #
        id_set2 = DatasetLinkingDataverse.objects.distinct('linkingdataverse__id'\
                    ).values_list('linkingdataverse__id', flat=True)

        # Get DvObject Ids of:
        #  - Dataverses that link to Dataverses
        #
        id_set3 = DataverseLinkingDataverse.objects.distinct('dataverse__id'\
                    ).values_list('dataverse__id', flat=True)

        #  Combine the ids into a list
        #
        dv_ids_to_exclude = set(list(id_set1) + list(id_set2) + list((id_set3)))

        #   Retrieve published Dataverses that aren't in the list above
        #
        dv_info_list = Dataverse.objects.select_related('dvobject'\
                    ).exclude(dvobject__id__in=dv_ids_to_exclude\
                    ).filter(**filter_params\
                    ).order_by(DVOBJECT_CREATEDATE_ATTR\
                    ).annotate(dv_id=F('dvobject__id'),\
                        create_date=F(DVOBJECT_CREATEDATE_ATTR),\
                        pub_date=F('dvobject__publicationdate')\
                    ).values('dv_id', 'name', 'alias'\
                            , 'create_date', 'pub_date'\
                            , 'affiliation'\
                    ).order_by('create_date', 'name')

        sql_query = str(q.query)

        records = []
        for dv_info in dv_info_list:
            single_rec = OrderedDict()
            single_rec['id'] = dv_info['dv_id']
            single_rec['name'] = dv_info['name']
            single_rec['alias'] = dv_info['alias']
            single_rec['url'] = '%s/dataverse/%s' % (settings.DATAVERSE_INSTALLATION_URL, dv_info['alias'])
            #single_rec['description'] = dv_info['description']
            single_rec['affiliation'] = dv_info['affiliation']
            single_rec['publication_date'] = dv_info['pub_date'].strftime(TIMESTAMP_MASK)
            single_rec['create_date'] = dv_info['create_date'].strftime(TIMESTAMP_MASK)
            records.append(single_rec)


        data_dict = OrderedDict()
        data_dict['count'] = len(records)
        data_dict['records'] = records

        return StatsResult.build_success_result(data_dict, sql_query)


    def get_dataset_subject_counts(self,  **extra_filters):
        """Dataset counts by subjet"""

        # Was an error found earlier?
        #
        if self.was_error_found():
            return self.get_error_msg_return()

        # -----------------------------------
        # (1) Build query filters
        # -----------------------------------

        # Retrieve the date parameters
        # -----------------------------------
        filter_params = self.get_date_filter_params()

        # -----------------------------------
        # Add extra filters from kwargs
        # -----------------------------------
        if extra_filters:
            for k, v in extra_filters.items():
                filter_params[k] = v

        # -----------------------------
        # Get the DatasetFieldType for subject
        # -----------------------------
        search_attrs = dict(name='subject',\
                            required=True,\
                            metadatablock__name='citation')
        try:
            ds_field_type = DatasetFieldType.objects.get(**search_attrs)
        except DatasetFieldType.DoesNotExist:
            return False, 'DatasetFieldType for Citation title not found.  (kwargs: %s)' % search_attrs

        # -----------------------------
        # Retrieve Dataset ids by time and published/unpublished
        # -----------------------------
        dataset_ids = Dataset.objects.select_related('dvobject'\
                        ).filter(**filter_params\
                        ).values_list('dvobject__id', flat=True)



        # -----------------------------
        # Get latest DatasetVersion ids
        # -----------------------------
        id_info_list = DatasetVersion.objects.filter(\
            dataset__in=dataset_ids\
            ).values('id', 'dataset_id', 'versionnumber', 'minorversionnumber'\
            ).order_by('dataset_id', '-id', '-versionnumber', '-minorversionnumber')

        # -----------------------------
        # Iterate through and get the DatasetVersion id
        #        of the latest version
        # -----------------------------
        dsv_ids = []
        last_dataset_id = None
        for idx, info in enumerate(id_info_list):
            if idx == 0 or info['dataset_id'] != last_dataset_id:
                dsv_ids.append(info['id'])

            last_dataset_id = info['dataset_id']

        # -----------------------------
        # Get the DatasetField ids
        # -----------------------------
        search_attrs2 = dict(datasetversion__id__in=dsv_ids,\
                        datasetfieldtype__id=ds_field_type.id)
        ds_field_ids = DatasetField.objects.select_related('datasetfieldtype').filter(**search_attrs2).values_list('id', flat=True)

        # -----------------------------
        # Finally, get the ControlledVocabularyValues
        # -----------------------------
        ds_values = DatasetFieldControlledVocabularyValue.objects.select_related('controlledvocabularyvalues'\
            ).filter(datasetfield__in=ds_field_ids\
            ).annotate(subject=F('controlledvocabularyvalues__strvalue')
            ).values('subject'\
            ).annotate(cnt=models.Count('controlledvocabularyvalues__id')\
            ).values('subject', 'cnt'\
            ).order_by('-cnt')


        # -----------------------------
        # Iterate through the vocab values,
        # process the totals, calculate percentage
        # -----------------------------
        running_total = 0
        formatted_records = []  # move from a queryset to a []
        total_count = sum([rec['cnt'] for rec in ds_values]) + 0.00

        for info in ds_values:
            rec = OrderedDict()
            rec['subject'] = info['subject']

            # count
            rec['count'] = info['cnt']
            rec['total_count'] = int(total_count)

            # percent
            float_percent = info['cnt'] / total_count
            rec['percent_string'] = '{0:.1%}'.format(float_percent)
            rec['percent_number'] = float("%.3f" %(float_percent))

            # total count

            formatted_records.append(rec)

        data_dict = OrderedDict()
        data_dict['record_count'] = len(formatted_records)
        data_dict['records'] = formatted_records

        return StatsResult.build_success_result(data_dict)




    def make_month_lookup(self, stats_queryset):
        """Make a dict from the 'stats_queryset' with a key of YYYY-MMDD"""

        d = {}
        for info in stats_queryset:
            d[info['month_year']] = info
        return d


    def create_month_year_iterator(self, create_date_info, pub_date_info):
        """
        Get the range of create and pub dates
        """
        #import ipdb; ipdb.set_trace()
        if len(pub_date_info) == 0:
            # No pub date, return create date ranges
            if len(create_date_info) == 1:
                # create start/end dates are the same
                first_date = create_date_info[0]
                last_date = first_date
            elif len(create_date_info) > 1:
                # different start/end dates
                first_date = create_date_info[0]
                last_date = create_date_info[-1]
            else:
                # no start/end dates for pub date or create date
                return None   # No pub date or create date info
        else:
            # We have a pub date and a create date
            first_date = min([create_date_info[0], pub_date_info[0]])
            last_date = max([create_date_info[-1], pub_date_info[-1]])

        return month_year_iterator(first_date['year_num'],\
                            first_date['month_num'],\
                            last_date['year_num'],\
                            last_date['month_num'],\
                            )

    def get_dataset_counts_by_create_date_and_pub_date(self):
        """INCOMPLETE - Combine create and publication date stats info"""

        if self.was_error_found():
            return self.get_error_msg_return()

        # (1) Get the stats for datasets *created* each month
        #
        success, create_date_info = self.get_dataset_counts_by_create_date()
        if not success:
            self.add_error('Failed to retrieve dataset counts by create date')

        # (2) Get the stats for datasets *published* each month
        #
        success, pub_date_info = self.get_dataset_counts_by_publication_date()
        if not success:
            self.add_error('Failed to retrieve dataset counts by publication date')

        # (3) Make dicts of these stats.  Key is YYYY-MMDD
        #
        create_date_dict = self.make_month_lookup(create_date_info)
        pub_date_dict = self.make_month_lookup(pub_date_info)

        # (4) Get list of months in YYYY-MMDD format to iterate through
        #
        month_iterator = self.create_month_year_iterator(create_date_info, pub_date_info)
        #import ipdb; ipdb.set_trace()
        #for yyyy, mm in
        #print 'pub_date_info', len(pub_date_info)

        #months_in_pub_only = set(pub_date_dict.keys()) - set(create_date_dict.keys())

        # Iterate through create_date_info
        formatted_dict = OrderedDict()
        formatted_list = []

        last_pub_running_total = 0
        for dataset_info in create_date_info:
            current_month = dataset_info['month_year']

            # Are there publication date numbers for this month?
            #
            pub_info = pub_date_dict.get(current_month, None)
            if pub_info:    # Yes, Add it to the create date dict
                dataset_info['pub_cnt'] = pub_info['cnt']
                dataset_info['pub_running_total'] = pub_info['running_total']
                last_pub_running_total = pub_info['running_total']
            else:           # No, Add last running total to the create date dict
                dataset_info['pub_cnt'] = 0    # No datasets published this month
                dataset_info['pub_running_total'] = last_pub_running_total

            formatted_dict[current_month] = dataset_info
            formatted_list.append(dataset_info)

        # NOT DONE - NEED TO CHECK for MISSING MONTHS ADD PUB ONLY MONTHS! by using month_iterator
        return True, formatted_list
