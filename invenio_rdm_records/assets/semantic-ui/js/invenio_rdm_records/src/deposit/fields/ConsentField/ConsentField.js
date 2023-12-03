// This file is part of Invenio-RDM-Records
// Copyright (C) 2020-2023 CERN.
// Copyright (C) 2020-2022 Northwestern University.
// Copyright (C) 2021 Graz University of Technology.
//
// Invenio-RDM-Records is free software; you can redistribute it and/or modify it
// under the terms of the MIT License; see LICENSE file for more details.

import React, { Component } from "react";
import PropTypes from "prop-types";
// import { useFormikContext } from "formik";
import { Checkbox } from "semantic-ui-react";
import { i18next } from "@translations/invenio_rdm_records/i18next";

export const ConsentField = (props) => {
  const { fieldPath, options, label, required, recordUI } = props;
  const trueValue = true;
  // const { setFieldValue } = useFormikContext();
  // setFieldValue("custom_fields.rik_consent", trueValue);
  return (
    <>
      <Checkbox
        label={i18next.t(
          "I have the right to upload this knowledge"
        )}
        fieldPath={fieldPath}
        // onChange={handleOnChangeConsent}
        // disabled={trueValue}
        // onChecked={handleOnChangeConsent}
        // defaultChecked
        checked={trueValue}
      />
    </>
  );
};

ConsentField.propTypes = {
  fieldPath: PropTypes.string.isRequired,
  label: PropTypes.string,
  options: PropTypes.shape({
    type: PropTypes.arrayOf(
      PropTypes.shape({
        icon: PropTypes.string,
        text: PropTypes.string,
        value: PropTypes.string,
      })
    ),
    lang: PropTypes.arrayOf(
      PropTypes.shape({
        text: PropTypes.string,
        value: PropTypes.string,
      })
    ),
  }).isRequired,
  required: PropTypes.bool,
  recordUI: PropTypes.object,
};

ConsentField.defaultProps = {
  label: i18next.t("Consent"),
  required: false,
  recordUI: undefined,
};
